import json
import logging

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)


def _delta(payload: dict) -> str:
    body = {"choices": [{"delta": payload}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def build_agent(model, workflows: dict):
    catalog = "\n".join(f"- {name}: {spec['description']}" for name, spec in workflows.items())
    system_prompt = (
        "你是调度助手，你叫‘测试助手’，用中文回答。\n"
        "命中下面工作流的任务，调用 run_workflow，不要自己做。其他问题直接回答。\n"
        "工作流看不到这段对话，已知事实全部写进 task。\n\n"
        f"可执行的工作流：\n{catalog}"
    )

    @tool
    def run_workflow(name: str, task: str) -> str:
        """执行一个固定工作流。name 使用系统提示词里列出的名称。task 写上用户原话和已经确认的事实。"""
        spec = workflows.get(name)
        if spec is None:
            return f"没有这个工作流，可用的有：{', '.join(workflows)}"
        logger.info("进入 %s: %s", name, task)
        state = spec["graph"].invoke({"question": task})
        reply = state.get("reply") or ""
        logger.info("离开 %s", name)
        return reply

    graph = create_agent(
        model=model,
        tools=[run_workflow],
        system_prompt=system_prompt,
        checkpointer=InMemorySaver(),
    )
    return graph


class Agent:
    def __init__(self, context_id: str, graph):
        self.context_id = context_id
        self.graph = graph

    def config(self):
        return {"configurable": {"thread_id": self.context_id}}

    def chat(self, msg: str):
        result = self.graph.invoke(
            {"messages": [{"role": "user", "content": msg}]},
            self.config(),
        )
        content = str(result["messages"][-1].text)
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    def chat_stream(self, msg: str):
        for message, _metadata in self.graph.stream(
            {"messages": [{"role": "user", "content": msg}]},
            self.config(),
            stream_mode="messages",
        ):
            if isinstance(message, ToolMessage):
                yield _delta({
                    "tool_calls": [{
                        "id": message.tool_call_id or "",
                        "type": "function_result",
                        "function": {"name": message.name or "", "arguments": ""},
                        "content": str(message.text),
                    }]
                })
                continue
            if not isinstance(message, AIMessageChunk):
                continue
            payload = {}
            text = str(message.text)
            if text:
                payload["content"] = text
            calls = []
            for call in message.tool_call_chunks or []:
                name = call.get("name") or ""
                arguments = call.get("args") or ""
                call_id = call.get("id") or ""
                if not (name or arguments or call_id):
                    continue
                calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                })
            if calls:
                payload["tool_calls"] = calls
            if payload:
                yield _delta(payload)
