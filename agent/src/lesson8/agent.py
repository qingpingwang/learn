import json

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from fs import VirtualFS

SYSTEM = "你是测试助手，用中文回答。"

PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
]

BRIEF = {
    "name": "brief",
    "description": "把一段较长的材料压成 5 行，写入 /workspace/brief.md。用户要求缩短、摘要成简报时调用。",
    "system_prompt": "把任务里的材料写成 5 行，写入 /workspace/brief.md。不要改 /skills 和 /memories。写完只回复这个路径。",
    "tools": [],
    "permissions": [
        FilesystemPermission(operations=["write"], paths=["/skills/**", "/memories/**"], mode="deny"),
    ],
}


def _profile() -> HarnessProfile:
    return HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({"execute"}),
    )


def build_agent(model, fs: VirtualFS):
    provider = get_model_provider(model) or "openai"
    identifier = get_model_identifier(model)
    register_harness_profile(provider, _profile())
    if identifier:
        register_harness_profile(f"{provider}:{identifier}", _profile())

    return create_deep_agent(
        model=model,
        system_prompt=SYSTEM,
        backend=fs,
        skills=["/skills/"],
        memory=["/memories/AGENTS.md"],
        permissions=PERMISSIONS,
        subagents=[BRIEF],
        checkpointer=InMemorySaver(),
    )


def _delta(payload: dict) -> str:
    body = {"choices": [{"delta": payload}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _text(result) -> str:
    messages = result["messages"] if isinstance(result, dict) else result.value["messages"]
    return str(messages[-1].text)


class Agent:
    def __init__(self, context_id: str, graph, fs: VirtualFS):
        self.context_id = context_id
        self.graph = graph
        self.fs = fs

    def config(self):
        return {"configurable": {"thread_id": self.context_id}}

    def chat(self, msg: str):
        self.fs.bind(self.context_id)
        result = self.graph.invoke(
            {"messages": [{"role": "user", "content": msg}]},
            self.config(),
        )
        return {"choices": [{"message": {"role": "assistant", "content": _text(result)}}]}

    def chat_stream(self, msg: str):
        self.fs.bind(self.context_id)
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
