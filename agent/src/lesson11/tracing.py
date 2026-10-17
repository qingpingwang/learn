"""第 11 课：给第 8 课的 deepagents agent 接上 langfuse。

第 8 课的代码一行不动：这里复用它的 build_agent / Agent / VirtualFS，
只在每次调用图的 config 里加一个 langfuse 的 CallbackHandler。
"""
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from langchain_openai import ChatOpenAI
from langfuse import get_client
from langfuse.langchain import CallbackHandler

HERE = Path(__file__).resolve().parent
LESSON8 = HERE.parent / "lesson8"
sys.path.insert(0, str(LESSON8))

from agent import Agent as Lesson8Agent, build_agent  # noqa: E402
from fs import VirtualFS  # noqa: E402

load_dotenv()

# 本地 langfuse 的项目 key，取自 docker 初始化用的 langfuse/.env
_lf = dotenv_values(HERE / "langfuse" / ".env")
os.environ.setdefault("LANGFUSE_BASE_URL", "http://127.0.0.1:3000")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", _lf.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY") or "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", _lf.get("LANGFUSE_INIT_PROJECT_SECRET_KEY") or "")

# 和第 8 课 main.py 一样搭一份：技能用第 8 课的，会话和记忆放在本课的 data 下
DATA = HERE / "data"
MEMORY = DATA / "memories" / "AGENTS.md"
MEMORY.parent.mkdir(parents=True, exist_ok=True)
if not MEMORY.exists():
    MEMORY.write_text("还没有需要跨会话记住的事实。\n", encoding="utf-8")
FS = VirtualFS(DATA / "sessions", skills=LESSON8 / "skills", memories=DATA / "memories")

model = ChatOpenAI(
    model=os.getenv("MODEL_NAME"),
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL"),
    timeout=120,
)
GRAPH = build_agent(model, FS)


def langfuse():
    return get_client()


class Agent(Lesson8Agent):
    """第 8 课的 Agent，只改 config：多一个 CallbackHandler 和几条 langfuse 元数据。"""

    def __init__(self, context_id: str):
        super().__init__(context_id, GRAPH, FS)

    def config(self):
        config = super().config()
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {
            "langfuse_session_id": self.context_id,
            "langfuse_user_id": "lesson11",
            "langfuse_tags": ["lesson11", "deepagents"],
        }
        return config
