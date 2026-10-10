from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
# 加载env文件
import os
from dotenv import load_dotenv
load_dotenv()
import json
import math
import requests
import logging
import uuid 
from datetime import datetime
# 打印到根目录的log文件
logging.basicConfig(level=logging.INFO, filename="./log/lesson3-2.log")
logger = logging.getLogger(__name__)

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model_name = os.getenv("MODEL_NAME")
max_tokens = int(os.getenv("MAX_TOKENS", 256))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


SYSTEM_PROMPT = "你是一个简洁的助手，你叫‘测试助手’，用中文回答。"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "按地址查询当前天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "地点，例如北京、上海市浦东新区",
                    }
                },
                "required": ["address"],
            },
        },
    }
]

# 上下文管理 id->context
context_list = {}

@app.post("/create_context")
def create_context():
    id = str(uuid.uuid4())
    context_list[id] = {
        "messages": []
    }
    return {"context_id": id}

@app.post("/delete_context")
def delete_context(context_id: str):
    if context_id not in context_list:
        return {"error": "context not found"}
    del context_list[context_id]
    return {"message": "context deleted"}

@app.post("/get_context")
def get_context(context_id: str):
    if context_id not in context_list:
        return {"error": "context not found"}
    return context_list[context_id]

# 对齐 langchain_core.messages.utils.count_tokens_approximately
# 英文大约 4 个字符一个 token，每条消息再加 3 个角色标记
def estimate_tokens(messages, chars_per_token=4, extra_tokens_per_message=3):
    total = 0
    for message in messages:
        chars = 0
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif content is not None:
            chars += len(json.dumps(content, ensure_ascii=False))
        if message.get("tool_calls"):
            chars += len(json.dumps(message["tool_calls"], ensure_ascii=False))
        if message.get("reasoning_content"):
            chars += len(message["reasoning_content"])
        if message.get("name"):
            chars += len(message["name"])
        total += math.ceil(chars / chars_per_token) + extra_tokens_per_message
    return total


def align_tool_messages(full, truncated):
    # 1.过滤出完整的工具调用消息
    message_call = {}
    for message in full:
        if message.get("role") != "assistant":
            continue
        # 多个工具调用，一次下发的情况，需要记录下所有工具调用id
        for call in message.get("tool_calls") or []:
            # 函数调用判断
            if call.get("type") != "function":
                continue
            message_call[call["id"]] = message

    # 2.截断列表里已经配上的调用记下来，剩下的 tool 结果才是没闭合的
    function_call_ids = []
    orphan_ids = []
    for message in truncated:
        if message.get("type") == "tool_call":
            for call in message.get("tool_calls") or []:
                function_call_ids.append(call["id"])
            continue
        call_id = message.get("tool_call_id")
        if message.get("role") == "tool" and call_id not in function_call_ids:
            orphan_ids.append(call_id)

    # 非闭合工具，把整条 tool_call 消息插到开头，考虑一次下发多次工具消息的情况，通过inserted过滤
    inserted = []
    # 3.倒叙遍历，因为工具调用结果是按顺序下发的，所以需要倒叙遍历
    for call_id in reversed(orphan_ids):
        message = message_call.get(call_id)
        if message is None or message in inserted:
            continue
        truncated.insert(0, message)
        inserted.append(message)


# 压缩上下文
# def compress_context(messages, system_prompt: str):
#     # 预估token
#     tokens = estimate_tokens([{"role": "system", "content": system_prompt}, *messages])
#     logger.info(f"预估token: {tokens}，max_tokens: {max_tokens}")
#     if tokens < max_tokens:
#         return
#     logger.info(f"触发上下文压缩！")
#     full = list(messages)
#     del messages[:-5]
#     align_tool_messages(full, messages)

def summary_context(messages):
    # 调用大模型总结。单独组一份消息，避免压缩中间件改到原来的上下文
    summary_prompt = "把下面的对话压缩成一段简短摘要，保留用户目标、已确认的事实和未完成事项。只输出摘要。"
    response = post_completions(
        [{"role": "user", "content": f"{summary_prompt}\n{json.dumps(messages, ensure_ascii=False)}"}],
        stream=False,
    ).json()
    return response["choices"][0]["message"].get("content") or ""

def cutoff_index(messages, keep):
    # 保留最后 keep 条，切口不能落在 tool 结果上，要退到这组 tool_call 的开头
    if len(messages) <= keep:
        return 0
    cutoff = len(messages) - keep
    while cutoff > 0 and messages[cutoff].get("role") == "tool":
        cutoff -= 1
    return cutoff

def compress_context(messages, system_prompt: str):
    # 预估token
    tokens = estimate_tokens([{"role": "system", "content": system_prompt}, *messages])
    logger.info(f"预估token: {tokens}，max_tokens: {max_tokens}")
    if tokens < max_tokens:
        return
    cutoff = cutoff_index(messages, 5)
    if cutoff <= 0:
        return
    logger.info(f"触发上下文压缩！")
    summary = summary_context(messages[:cutoff])
    logger.info(f"总结: {summary}")
    # 切口之前换成摘要，切口之后原样留下
    messages[:] = [{"role": "user", "content": f"此前对话摘要：{summary}"}, *messages[cutoff:]]

# 中间件列表
middleware_list = [compress_context]


def dynamic_system_prompt(system_prompt: str) -> str:
    return f"{system_prompt}当前的系统时间是：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。"

def completion_body(messages, stream=False):
    system_prompt = dynamic_system_prompt(SYSTEM_PROMPT)
    # 调用中间件
    for middleware in middleware_list:
        middleware(messages, system_prompt)
    body = {
        "model": model_name,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "tools": TOOLS,
    }
    if stream:
        body["stream"] = True
    return body

# 发送请求
def post_completions(messages, stream=False):
    return requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=completion_body(messages, stream),
        stream=stream,
        timeout=120,
    )


def get_weather(address):
    return f"{address}是晴天!"


# 执行工具
def run_tool(name, arguments):
    args = json.loads(arguments or "{}")
    if name != "get_weather":
        return "工具调用失败"
    return get_weather(args.get("address", ""))


# 追加工具结果到上下文
def append_tool_result(messages, call_id, name, arguments):
    content = run_tool(name, arguments)
    messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
    return content


# 消息输入
class MessageIn(BaseModel):
    message: str
    context_id: str


# 加载消息
def load_messages(body: MessageIn):
    messages = context_list[body.context_id]["messages"]
    messages.append({"role": "user", "content": body.message})
    return messages


# 处理工具（递归）
def handle_tools(messages: list, response: dict):
    message = response["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    messages.append(message)
    if not tool_calls:
        return response
    for call in tool_calls:
        append_tool_result(messages, call["id"], call["function"]["name"], call["function"]["arguments"])
    return handle_tools(messages, post_completions(messages).json())


# 非流式输出
@app.post("/chat")
def chat(body: MessageIn):
    logger.info(f"receive_message: {body.message}")
    messages = load_messages(body)
    response = post_completions(messages).json()
    logger.info("response: %s", json.dumps(response, ensure_ascii=False, indent=2))
    # 解析工具调用
    return handle_tools(messages, response)


def event_stream(messages):
    response = post_completions(messages, stream=True)
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


@app.post("/stream_chat")
def stream_chat(body: MessageIn):
    messages = load_messages(body)

    def generate():
        # 循环调用流式事件，有工具执行
        while True:
            # 单次调用的工具调用、推理内容、消息内容
            function_calls = []
            reasoning_content = ""
            message_content = ""
            for chunk in event_stream(messages):
                for choice in chunk["choices"]:
                    tool_calls = choice["delta"].get("tool_calls", [])
                    reasoning_content += choice["delta"].get("reasoning_content") or ""
                    message_content += choice["delta"].get("content") or ""
                    for tool_call in tool_calls:
                        if "id" in tool_call:
                            function_calls.append({
                                "id": tool_call["id"],
                                "name": "",
                                "arguments": ""
                            })
                        if "function" in tool_call:
                            function_calls[-1]["name"] += tool_call["function"].get("name") or ""
                            function_calls[-1]["arguments"] += tool_call["function"].get("arguments") or ""
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            # 如果没有工具需要执行，则sse结束
            if not function_calls:
                if message_content:
                    messages.append({"role": "assistant", "content": message_content})
                break
            # 工具消息组装进上下文
            messages.append({
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
                content = append_tool_result(
                    messages, function_call["id"], function_call["name"], function_call["arguments"]
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
                    }]}
                yield f"data: {json.dumps(yield_data, ensure_ascii=False)}\n\n"
        # 单次调用的结束，进入下一轮推理

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 8000
    logger.info(f"Server is running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
