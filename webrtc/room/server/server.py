"""WebRTC 对话房间后端：FastAPI 信令 + aiortc SFU。

所有地址挂在 /webrtc 下：/webrtc/api/* 是接口，/webrtc/ws 是信令，
其余是前端 web/ 目录的静态文件（目录由 WEBRTC_WEB_DIR 指定，相对 server/）。

每个参与者和服务器之间只建一条 RTCPeerConnection，始终由服务器发 offer：
- 上行：服务器先开两个只收的 transceiver（音频、视频），参与者把自己的轨道挂上去；
- 下行：服务器用 MediaRelay 把房间里其他人的轨道转发给他，有人进出时重新发 offer。

aiortc 是 WebRTC 的 Python 实现，服务器端的 ICE、DTLS/SRTP、RTP、编解码都由它完成。
注意：aiortc 的转发不是纯 RTP 包转发。收到的媒体会先解码成帧，MediaRelay 分发的是帧，
每路下行再各自编码一次（VP8/Opus），CPU 开销随人数平方增长，所以每个房间限制人数。
"""

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpTransceiver,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamTrack
from aiortc.sdp import candidate_from_sdp
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("room")

HERE = Path(__file__).parent
WEB_DIR = (HERE / os.environ.get("WEBRTC_WEB_DIR", "../web")).resolve()
STUN_URLS = os.environ.get(
    "WEBRTC_STUN", "stun:stun.miwifi.com:3478,stun:stun.chat.bilibili.com:3478"
).split(",")
MAX_ROOMS = int(os.environ.get("WEBRTC_MAX_ROOMS", "20"))
MAX_PEERS = int(os.environ.get("WEBRTC_MAX_PEERS", "4"))
IDLE_ROOM_TTL = 600  # 建了房间没人进，10 分钟后回收
ANSWER_TIMEOUT = 15

# 【转发】一个源轨道只被读一次，MediaRelay 给每个订阅者一份独立的代理轨道
relay = MediaRelay()


@dataclass
class Peer:
    id: str
    name: str
    ws: WebSocket
    pc: RTCPeerConnection
    # 上行：服务器只收的两个 transceiver，和收到的轨道
    upload: list[RTCRtpTransceiver] = field(default_factory=list)
    tracks: list[MediaStreamTrack] = field(default_factory=list)
    # 下行：源轨道 -> (transceiver, 源参与者 id)
    forwarded: dict[MediaStreamTrack, tuple[RTCRtpTransceiver, str]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    answer: asyncio.Future | None = None
    closed: bool = False

    async def send(self, msg: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(msg))
        except Exception:
            pass


@dataclass
class Room:
    id: str
    password: str
    created: float = field(default_factory=time.monotonic)
    peers: dict[str, Peer] = field(default_factory=dict)


rooms: dict[str, Room] = {}
background: set[asyncio.Task] = set()


def ice_config() -> RTCConfiguration:
    # aiortc 只用第一个 STUN 地址
    return RTCConfiguration(iceServers=[RTCIceServer(urls=STUN_URLS[0])])


def new_room_id() -> str:
    while True:
        rid = f"{secrets.randbelow(10**6):06d}"
        if rid not in rooms:
            return rid


def reap_idle_rooms() -> None:
    now = time.monotonic()
    for rid in [r.id for r in rooms.values() if not r.peers and now - r.created > IDLE_ROOM_TTL]:
        rooms.pop(rid, None)


# ---------- 重新协商：有人加入或离开时，服务器给受影响的人重新发 offer ----------

async def negotiate(room: Room, peer: Peer) -> None:
    """把 peer 的上下行调整到房间当前状态，有变化就发 offer，等 answer。"""
    async with peer.lock:
        if peer.closed:
            return
        changed = False

        # 【接收】上行：服务器开两个只收的 transceiver，浏览器 addTrack 后会落在这两路上
        if not peer.upload:
            peer.upload = [
                peer.pc.addTransceiver("audio", direction="recvonly"),
                peer.pc.addTransceiver("video", direction="recvonly"),
            ]
            changed = True

        wanted = {t: other.id for other in room.peers.values() if other is not peer for t in other.tracks}

        # 离开的人：源轨道结束后 sender 也就停了，这一路改成 inactive，不再复用
        for track in [t for t in peer.forwarded if t not in wanted]:
            transceiver, _ = peer.forwarded.pop(track)
            transceiver.direction = "inactive"
            changed = True

        # 【转发】新来的人：每条轨道经 MediaRelay 订阅一份，开一个只发的 transceiver（SFU 的下行）
        for track, src in wanted.items():
            if track not in peer.forwarded:
                transceiver = peer.pc.addTransceiver(relay.subscribe(track), direction="sendonly")
                peer.forwarded[track] = (transceiver, src)
                changed = True

        if not changed:
            return

        # 【信令】aiortc 不支持 trickle，setLocalDescription 时收集完 ICE 候选，直接写进 offer 的 SDP
        await peer.pc.setLocalDescription(await peer.pc.createOffer())
        peer.answer = asyncio.get_running_loop().create_future()
        await peer.send({
            "type": "offer",
            "sdp": peer.pc.localDescription.sdp,
            "upload": {t.kind: t.mid for t in peer.upload},
            "mids": {t.mid: src for t, src in peer.forwarded.values()},
            "names": {p.id: p.name for p in room.peers.values()},
        })
        try:
            sdp = await asyncio.wait_for(peer.answer, ANSWER_TIMEOUT)
        finally:
            peer.answer = None
        await peer.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

        # 【接收】第一次 answer 回来，receiver.track 就是他的上行音视频轨道（aiortc 在这里解码），
        # 然后让房间里其他人重新协商，来订阅这两条轨道
        if not peer.tracks:
            peer.tracks = [t.receiver.track for t in peer.upload if t.receiver.track]
            if peer.tracks:
                sync_room(room, skip=peer)


async def safe_negotiate(room: Room, peer: Peer) -> None:
    try:
        await negotiate(room, peer)
    except Exception as e:
        # 协商途中对方离开（pc 已关闭）或超时没回 answer
        if not peer.closed:
            log.warning("peer %s negotiation failed: %r", peer.id, e)


def sync_room(room: Room, skip: Peer | None = None) -> None:
    for p in list(room.peers.values()):
        if p is not skip:
            task = asyncio.create_task(safe_negotiate(room, p))
            background.add(task)
            task.add_done_callback(background.discard)


# 【信令】浏览器 trickle 过来的 ICE 候选
async def add_candidate(peer: Peer, c: dict | None) -> None:
    if not c or not c.get("candidate"):
        return
    try:
        cand = candidate_from_sdp(c["candidate"].split(":", 1)[1])
        cand.sdpMid = c.get("sdpMid")
        cand.sdpMLineIndex = c.get("sdpMLineIndex")
        await peer.pc.addIceCandidate(cand)
    except Exception as e:
        log.debug("skip candidate %s: %s", c.get("candidate"), e)


# 离开：关掉他的连接（源轨道随之结束），通知其他人，再让其他人重新协商去掉这几路
async def leave(room: Room, peer: Peer) -> None:
    peer.closed = True
    room.peers.pop(peer.id, None)
    if peer.answer and not peer.answer.done():
        peer.answer.cancel()
    await peer.pc.close()
    if not room.peers:
        rooms.pop(room.id, None)
        return
    for p in room.peers.values():
        await p.send({"type": "leave", "peer": peer.id})
    sync_room(room)


app = FastAPI()


@app.get("/webrtc/api/config")
async def config() -> dict:
    return {"iceServers": [{"urls": u} for u in STUN_URLS]}


# 【房间】新建房间：房间号和密码都用 secrets 生成，房间只存在内存里
@app.post("/webrtc/api/rooms")
async def create_room() -> dict:
    reap_idle_rooms()
    if len(rooms) >= MAX_ROOMS:
        raise HTTPException(503, "房间数已满")
    room = Room(id=new_room_id(), password=secrets.token_urlsafe(6))
    rooms[room.id] = room
    # 密码只在这里返回一次，服务器之后只用来校验
    return {"room": room.id, "password": room.password}


# 【信令】WebSocket：第一条消息必须是 join，校验房间号和密码后才进入房间
@app.websocket("/webrtc/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await ws.close()
        return

    room = rooms.get(str(hello.get("room", "")))
    password = str(hello.get("password", ""))
    # 【房间】compare_digest 按常量时间比较，不因比较提前结束泄露密码信息
    if hello.get("type") != "join" or not room or not secrets.compare_digest(
        password.encode(), room.password.encode()
    ):
        await ws.send_text(json.dumps({"type": "error", "message": "房间号或密码错误"}))
        await ws.close()
        return
    if len(room.peers) >= MAX_PEERS:
        await ws.send_text(json.dumps({"type": "error", "message": f"房间最多 {MAX_PEERS} 人"}))
        await ws.close()
        return

    name = str(hello.get("name") or "").strip()[:20] or "访客"
    peer = Peer(id=secrets.token_hex(4), name=name, ws=ws, pc=RTCPeerConnection(ice_config()))
    # 【信令】广播给房间里已有的人：有人加入
    for p in room.peers.values():
        await p.send({"type": "join", "peer": peer.id, "name": name})
    room.peers[peer.id] = peer
    await peer.send({
        "type": "joined",
        "peer": peer.id,
        "peers": {p.id: p.name for p in room.peers.values() if p is not peer},
    })
    # 给新人发第一个 offer（上行两路 + 已有的人的轨道）
    sync_room(room)

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "answer" and peer.answer and not peer.answer.done():
                peer.answer.set_result(msg["sdp"])
            elif kind == "candidate":
                await add_candidate(peer, msg.get("candidate"))
    except (WebSocketDisconnect, ValueError, KeyError):
        pass
    finally:
        await leave(room, peer)


# 前端静态文件。站点 nginx 会把 /webrtc/ 重定向到 /webrtc，所以入口单独返回 index.html
@app.get("/webrtc")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/webrtc", StaticFiles(directory=WEB_DIR, html=True), name="web")
