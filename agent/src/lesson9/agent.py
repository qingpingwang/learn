import json
import os
import queue
import threading
from pathlib import Path

from deepseek_harness import DeepSeekHarness

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
HOME = DATA / "home"            # dsh_home：profile、会话 JSONL 都在这
SESSIONS = DATA / "sessions"    # 每个会话一个工作目录
PATCH = HERE / "patches" / "lesson9.patch.yml"   # 手写的 patch，dsh 用 --patch 读它


def build_harness(cwd: Path) -> DeepSeekHarness:
    return DeepSeekHarness(
        profile="sdk-minimal",
        patches=(str(PATCH),),
        dsh_home=str(HOME),
        cwd=str(cwd),
        provider="deepseek-official",
        model=os.getenv("MODEL_NAME"),
        api_key=os.getenv("API_KEY"),     # 只进子进程的 DEEPSEEK_API_KEY
        base_url=os.getenv("BASE_URL"),
        request_timeout_seconds=300,
        runtime_cwd=str(HERE),            # dsh 进程的工作目录：patch 里 config 的相对路径从这里算
    )


def _delta(payload: dict) -> str:
    body = {"choices": [{"delta": payload}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _texts(parts) -> str:
    out = []
    for part in parts or []:
        if part.get("type") == "text":
            out.append(part.get("text", ""))
        elif part.get("type") == "tool-result":
            out.append(_texts(part.get("content")))
    return "".join(out)


def _translate(event: dict):
    """把 dsh 的 session.event 翻译成前几课的 SSE delta。"""
    kind = event.get("type", "")
    data = event.get("data") or {}
    if kind == "assistant/message":
        text = _texts((data.get("message") or {}).get("content"))
        return {"content": text} if text else None
    if kind == "tool/call":
        return {"tool_calls": [{
            "id": data.get("callId", ""),
            "type": "function",
            "function": {"name": data.get("name", ""), "arguments": data.get("arguments", "")},
        }]}
    if kind == "tool/result":
        message = data.get("message") or {}
        return {"tool_calls": [{
            "id": (message.get("source") or {}).get("callId", ""),
            "type": "function_result",
            "function": {"name": "", "arguments": ""},
            "content": _texts(message.get("content")),
        }]}
    return None


class Agent:
    """一个 context = 一个 dsh 子进程 + 一个工作目录，session_id 就用 context_id。"""

    def __init__(self, context_id: str):
        self.context_id = context_id
        self.cwd = SESSIONS / context_id / "workspace"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.harness = build_harness(self.cwd)
        self.lock = threading.Lock()

    def chat(self, msg: str):
        with self.lock:
            result = self.harness.run(msg, session_id=self.context_id)
        return {"choices": [{"message": {"role": "assistant", "content": result.final_response or ""}}]}

    def chat_stream(self, msg: str):
        events: queue.Queue = queue.Queue()
        done = object()

        def on_notification(note):
            if note.method == "session.event":
                events.put(note.payload.get("event") or {})

        def worker():
            try:
                with self.lock:
                    self.harness.run(msg, session_id=self.context_id,
                                     on_notification=on_notification)
            except Exception as exc:  # 出错也要让生成器结束
                events.put({"type": "assistant/message",
                            "data": {"message": {"content": [{"type": "text", "text": f"[error] {exc}"}]}}})
            finally:
                events.put(done)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            event = events.get()
            if event is done:
                break
            payload = _translate(event)
            if payload:
                yield _delta(payload)

    def close(self):
        self.harness.close()
