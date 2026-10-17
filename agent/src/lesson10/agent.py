import json
import os
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SESSIONS = DATA / "sessions"
CODEX_HOME = DATA / "codex_home"
CATALOG = HERE / "codex_home" / "models.json"

SYSTEM = (
    "你是测试助手，用中文回答。"
    "读写文件只在当前工作目录里，路径用相对路径，写文件用 apply_patch。"
    "用户的请求和某个技能对得上时，先读那个技能的 SKILL.md，再照着做，不要自己编格式。"
)

# shell 开着（模型要靠它读 SKILL.md），但 thread 用 workspace_write 沙箱 + deny_all 审批，只能动会话工作目录。
# 技能不用配：Codex 会自己扫会话目录往上的 .agents/skills，memo 放在 src/lesson10/.agents/skills 里。
CONFIG = """model = "{model}"
model_provider = "deepseek"
model_catalog_json = "{catalog}"
web_search = "disabled"

[model_providers.deepseek]
name = "deepseek"
base_url = "{base_url}"
wire_api = "responses"
env_key = "DEEPSEEK_API_KEY"
"""


def build_codex() -> Codex:
    """起一个 codex app-server 子进程，所有会话共用。"""
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    SESSIONS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (CODEX_HOME / "config.toml").write_text(
        CONFIG.format(
            model=os.getenv("CODEX_MODEL", "deepseek-flash"),
            catalog=CATALOG,
            base_url=os.getenv("BASE_URL", "https://api.deepseek.com").rstrip("/") + "/",
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["CODEX_HOME"] = str(CODEX_HOME)
    env["DEEPSEEK_API_KEY"] = os.getenv("API_KEY", "")
    # 关掉系统代理，和第九课一样
    env.setdefault("NO_PROXY", "*")
    env.setdefault("no_proxy", "*")
    codex = Codex(CodexConfig(env=env, cwd=str(SESSIONS)))
    codex.__enter__()
    return codex


def _delta(payload: dict) -> str:
    body = {"choices": [{"delta": payload}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _call(item: dict):
    """把 codex 的 item 翻译成前几课的 tool_call。"""
    kind = item.get("type")
    if kind == "commandExecution":
        return "shell", json.dumps({"command": item.get("command", "")}, ensure_ascii=False)
    if kind == "fileChange":
        paths = [c.get("path", "") for c in item.get("changes", [])]
        return "apply_patch", json.dumps({"paths": paths}, ensure_ascii=False)
    return None


def _result(item: dict) -> str:
    kind = item.get("type")
    if kind == "commandExecution":
        return f"exit={item.get('exit_code')} {item.get('aggregated_output', '')}".strip()
    if kind == "fileChange":
        return item.get("status", "")
    return str(item.get("result", ""))


class Agent:
    """一个 context 对应一个 codex thread，一个工作目录。"""

    def __init__(self, context_id: str, codex: Codex):
        self.context_id = context_id
        self.codex = codex
        self.cwd = SESSIONS / context_id / "workspace"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.thread = codex.thread_start(
            cwd=str(self.cwd),
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.deny_all,
            developer_instructions=SYSTEM,
        )

    def chat(self, msg: str):
        result = self.thread.run(msg)
        return {"choices": [{"message": {"role": "assistant", "content": result.final_response or ""}}]}

    def chat_stream(self, msg: str):
        turn = self.thread.turn(msg)
        for event in turn.stream():
            payload = event.payload
            data = payload.model_dump(mode="json", exclude_none=True) if hasattr(payload, "model_dump") else {}
            if event.method == "item/agentMessage/delta":
                yield _delta({"content": data.get("delta", "")})
                continue
            if event.method not in ("item/started", "item/completed"):
                continue
            item = data.get("item", {})
            call = _call(item)
            if call is None:
                continue
            name, arguments = call
            if event.method == "item/started":
                yield _delta({"tool_calls": [{
                    "id": item.get("id", ""),
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }]})
            else:
                yield _delta({"tool_calls": [{
                    "id": item.get("id", ""),
                    "type": "function_result",
                    "function": {"name": name, "arguments": ""},
                    "content": _result(item),
                }]})
