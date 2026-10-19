// WebRTC 对话房间前端。
// navigator.mediaDevices、RTCPeerConnection、MediaStream 都是浏览器内置 API，所以没有 import。
// 媒体链路：采集 -> 发送/编码 -> 信令协商 -> 接收/解码 -> 渲染，各段在下面分别标出。

// 页面、接口、信令都挂在 /webrtc 下，本地和线上同一套地址
const base = "/webrtc";
const $ = (id) => document.getElementById(id);
const status = (s) => { $("status").textContent = s; };

// 【采集】约束：统一小画面，不超过 640x360、15 帧；码率上限 500 kbps 在发送阶段设置
const MEDIA = {
  audio: true,
  video: { width: { ideal: 640, max: 640 }, height: { ideal: 360, max: 360 }, frameRate: { ideal: 15, max: 15 } },
};
const VIDEO_MAX_BITRATE = 500_000;

let ws, pc, local;
let mids = {};    // 下行 transceiver 的 mid -> 来源参与者 id
let names = {};   // 参与者 id -> 昵称

$("create").onclick = async () => {
  const r = await fetch(`${base}/api/rooms`, { method: "POST" });
  if (!r.ok) return status(`新建失败：${r.status}`);
  const { room, password } = await r.json();
  $("new-room").textContent = room;
  $("new-pass").textContent = password;
  $("created").style.display = "block";
  $("room").value = room;
  $("password").value = password;
};

$("join").onclick = () => join().catch((e) => { status(`出错：${e.message}`); leave(); });
$("hangup").onclick = () => leave();

$("mic").onclick = () => {
  const t = local.getAudioTracks()[0];
  t.enabled = !t.enabled;
  $("mic").textContent = t.enabled ? "静音" : "取消静音";
};
$("cam").onclick = () => {
  const t = local.getVideoTracks()[0];
  t.enabled = !t.enabled;
  $("cam").textContent = t.enabled ? "关摄像头" : "开摄像头";
};

async function join() {
  const room = $("room").value.trim();
  const password = $("password").value.trim();
  if (!room || !password) return status("请输入房间号和密码");
  $("join").disabled = true;

  // 【采集】getUserMedia 打开摄像头和麦克风，得到一路音频轨道 + 一路视频轨道（只在 HTTPS 或 localhost 下可用）
  local = await navigator.mediaDevices.getUserMedia(MEDIA);
  const { iceServers } = await (await fetch(`${base}/api/config`)).json();

  // 【信令】WebSocket 只传控制消息：join、offer/answer（SDP）、ICE 候选；音视频本身不走这里
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}${base}/ws`);
  ws.onopen = () => send({ type: "join", room, password, name: $("name").value.trim() });
  ws.onclose = () => { if (pc) { status("连接已断开"); leave(); } };
  // 信令按到达顺序逐条处理，避免 answer 和服务器的新 offer 同时操作 pc
  let queue = Promise.resolve();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    queue = queue.then(() => onSignal(msg, iceServers)).catch((err) => status(`出错：${err.message}`));
  };
}

function send(msg) { ws.send(JSON.stringify(msg)); }

async function onSignal(msg, iceServers) {
  switch (msg.type) {
    case "error":
      status(msg.message);
      leave();
      break;
    case "joined":
      names = msg.peers;
      showRoom();
      render();
      break;
    case "join":
      names[msg.peer] = msg.name;
      status(`${msg.name} 加入了房间`);
      break;
    case "leave":
      status(`${names[msg.peer] || "有人"} 离开了房间`);
      for (const [mid, peer] of Object.entries(mids)) if (peer === msg.peer) delete mids[mid];
      render();
      break;
    case "offer":
      // 【信令】协商始终由服务器发起：第一次带上行的两路，之后每次带上房间里其他人的轨道。
      // 浏览器回 answer；双方的编解码器、方向、ICE/DTLS 参数都在这对 SDP 里定下来
      if (!pc) createPc(iceServers);
      mids = msg.mids;
      Object.assign(names, msg.names);
      await pc.setRemoteDescription({ type: "offer", sdp: msg.sdp });
      if (!pc.getSenders().some((s) => s.track)) publish(msg.upload);
      await pc.setLocalDescription(await pc.createAnswer());
      send({ type: "answer", sdp: pc.localDescription.sdp });
      await limitVideo();
      render();
      break;
  }
}

// RTCPeerConnection：浏览器内置的 libwebrtc。和服务器之间只有这一条连接，上下行都在上面
function createPc(iceServers) {
  pc = new RTCPeerConnection({ iceServers });
  // 【信令】浏览器每找到一个 ICE 候选（本机地址、STUN 得到的公网地址）就发给服务器（trickle）
  pc.onicecandidate = (e) => { if (e.candidate) send({ type: "candidate", candidate: e.candidate.toJSON() }); };
  pc.onconnectionstatechange = () => status(`媒体连接：${pc.connectionState}`);
}

// 【发送/编码】addTrack 把本地轨道交给 RTCPeerConnection。之后的编码（VP8/H.264、Opus）、
// RTP 打包、SRTP 加密、GCC 拥塞控制都由浏览器内置的 libwebrtc 完成，页面代码不接触编码。
// 第一次 offer：addTrack 会复用同类型里第一个空的 transceiver，也就是服务器开好的上行两路
function publish(upload) {
  for (const track of local.getTracks()) {
    const sender = pc.addTrack(track, local);
    const t = pc.getTransceivers().find((x) => x.sender === sender);
    if (t.mid !== upload[track.kind]) throw new Error(`上行 ${track.kind} 没有落在服务器给的 mid 上`);
  }
}

// 【发送/编码】setParameters 只给编码器设上限（码率 500 kbps、15 帧），实际码率由拥塞控制在上限内调整。
// 分辨率已在采集时限制到 360p，所以不再用 scaleResolutionDownBy
async function limitVideo() {
  const sender = pc.getSenders().find((s) => s.track && s.track.kind === "video");
  if (!sender) return;
  const params = sender.getParameters();
  if (!params.encodings || !params.encodings.length) return;
  params.encodings[0].maxBitrate = VIDEO_MAX_BITRATE;
  params.encodings[0].maxFramerate = 15;
  await sender.setParameters(params);
}

function showRoom() {
  $("lobby").style.display = "none";
  $("room-view").style.display = "block";
  $("cur-room").textContent = $("room").value.trim();
}

// 【接收/解码】服务器转发来的每一路在浏览器里是一个 transceiver，receiver.track 就是 ontrack 会给出的那条轨道。
// 这里不用 ontrack：同一个 mid 的归属按服务器 offer 里的 mids 来定，setRemoteDescription 后统一按 mid 取轨道。
// 解码、抖动缓冲（jitter buffer）、音画同步都由浏览器完成。
// 按 mids 把下行轨道归到各个参与者，每人一个小窗
function render() {
  const grid = $("grid");
  const streams = {};
  for (const t of pc ? pc.getTransceivers() : []) {
    const peer = mids[t.mid];
    if (!peer) continue;
    (streams[peer] ||= new MediaStream()).addTrack(t.receiver.track);
  }
  const want = new Set(["self", ...Object.keys(streams)]);
  for (const el of [...grid.children]) if (!want.has(el.dataset.peer)) el.remove();

  tile("self", "我", local, true);
  for (const [peer, stream] of Object.entries(streams)) tile(peer, names[peer] || peer, stream, false);
}

// 【渲染】把 MediaStream 交给 <video>.srcObject，浏览器负责播放；小窗 240x135，见 style.css。
// 自己的小窗静音，避免本地回声
function tile(peer, label, stream, muted) {
  let el = $("grid").querySelector(`[data-peer="${peer}"]`);
  if (!el) {
    el = document.createElement("div");
    el.className = "tile";
    el.dataset.peer = peer;
    el.innerHTML = "<video autoplay playsinline></video><span></span>";
    $("grid").appendChild(el);
  }
  const video = el.querySelector("video");
  video.muted = muted;
  const old = video.srcObject;
  const same = old && old.getTracks().length === stream.getTracks().length
    && stream.getTracks().every((t) => old.getTracks().includes(t));
  if (!same) video.srcObject = stream;
  el.querySelector("span").textContent = label;
}

function leave() {
  if (ws) { ws.onclose = null; ws.close(); }
  if (pc) pc.close();
  if (local) local.getTracks().forEach((t) => t.stop());
  ws = pc = local = null;
  mids = {}; names = {};
  $("grid").innerHTML = "";
  $("room-view").style.display = "none";
  $("lobby").style.display = "block";
  $("join").disabled = false;
}
