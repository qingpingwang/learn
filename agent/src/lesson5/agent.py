import logging
import requests
import json

logger = logging.getLogger(__name__)

def get_weather(address):
    return f"{address}的天气是晴天!"

class Agent:
    def __init__(
        self,
        context_id,
        api_key,
        base_url,
        model_name,
        max_tokens,
        system_prompt="You are a helpful assistant.",
        dynamic_system_prompt=None,
        tools=None,
        middlewares=None,
    ):
        self.context_id = context_id
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.dynamic_system_prompt = dynamic_system_prompt
        self.tools = [] if tools is None else list(tools)
        self.middlewares = [] if middlewares is None else list(middlewares)
        # 已加载的 skill 正文，每轮拼进系统提示词，不进 messages，避免被压缩掉
        self.loaded_skills = {}
        # 上下文
        self.messages = []
        # 工具处理函数
        self.tool_handlers = {}

    # 执行工具
    def run_tool(self, name, arguments):
        args = json.loads(arguments or "{}")
        handler = self.tool_handlers.get(name)
        if handler is None:
            return "工具调用失败"
        return handler(args)

    # 追加工具结果到上下文
    def append_tool_result(self, call_id, name, arguments):
        content = self.run_tool(name, arguments)
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        return content

    def add_middleware(self, middleware):
        self.middlewares.append(middleware)

    def post_completions(self, body, stream=False):
        return requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            stream=stream,
            timeout=120,
        )

    def chat(self, msg):
        # 1. 组装消息
        body = self.completion_body(msg)
        # 2. 发送请求、处理工具
        while True:
            response = self.post_completions(body).json()
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            self.messages.append(message)
            if not tool_calls:
                return response
            for call in tool_calls:
                self.append_tool_result(call["id"], call["function"]["name"], call["function"]["arguments"])
            body = self.request_body()

    def event_stream(self, body):
        response = self.post_completions(body, stream=True)
        response.raise_for_status()
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                text = line.decode()
                if not text.startswith("data: "):
                    continue
                data = text[6:]
                if data == "[DONE]":
                    break
                yield json.loads(data)
        finally:
            response.close()

    def chat_stream(self, msg):
        # 1. 组装消息
        body = self.completion_body(msg, stream=True)
        # 2. 循环调用流式事件，有工具执行
        while True:
            function_calls = []
            reasoning_content = ""
            message_content = ""
            for chunk in self.event_stream(body):
                for choice in chunk["choices"]:
                    tool_calls = choice["delta"].get("tool_calls") or []
                    reasoning_content += choice["delta"].get("reasoning_content") or ""
                    message_content += choice["delta"].get("content") or ""
                    for tool_call in tool_calls:
                        if "id" in tool_call:
                            function_calls.append({
                                "id": tool_call["id"],
                                "name": "",
                                "arguments": "",
                            })
                        if "function" in tool_call:
                            function_calls[-1]["name"] += tool_call["function"].get("name") or ""
                            function_calls[-1]["arguments"] += tool_call["function"].get("arguments") or ""
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            # 如果没有工具需要执行，则sse结束
            if not function_calls:
                if message_content:
                    self.messages.append({"role": "assistant", "content": message_content})
                break
            # 工具消息组装进上下文
            self.messages.append({
                "role": "assistant",
                "content": "",
                "reasoning_content": reasoning_content,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for call in function_calls
                ],
            })
            # 工具执行结果组装进上下文
            for function_call in function_calls:
                content = self.append_tool_result(
                    function_call["id"], function_call["name"], function_call["arguments"]
                )
                yield_data = {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "id": function_call["id"],
                                "type": "function_result",
                                "function": {"name": function_call["name"], "arguments": function_call["arguments"]},
                                "content": content,
                            }]
                        }
                    }]
                }
                yield f"data: {json.dumps(yield_data, ensure_ascii=False)}\n\n"
            body = self.request_body(stream=True)

    def request_body(self, stream=False):
        system_prompt = self.dynamic_system_prompt(self, self.system_prompt) if self.dynamic_system_prompt is not None else self.system_prompt
        # 调用中间件
        for middleware in self.middlewares:
            middleware(self, system_prompt)
        body = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system_prompt}, *self.messages],
            "tools": self.tools,
        }
        if stream:
            body["stream"] = True
        return body

    def completion_body(self, msg: str, stream=False):
        self.messages.append({"role": "user", "content": msg})
        return self.request_body(stream)
