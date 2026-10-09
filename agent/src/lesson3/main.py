from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
# 加载env文件
import os
from dotenv import load_dotenv
load_dotenv()
import asyncio
import json
import requests
import logging
import uuid
# 打印到根目录的log文件
logging.basicConfig(level=logging.INFO, filename="./log/lesson3.log")
logger = logging.getLogger(__name__)

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model_name = os.getenv("MODEL_NAME")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


SYSTEM_PROMPT = "你是一个简洁的助手，用中文回答。"

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


def post_request(url, payload):
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.post(url, headers=headers, json=payload)
    return response.json()


def get_weather(address):
    return f"{address}是晴天!"


def handle_tools(messages: list, response: dict):
    message = response["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        messages.append(message)
        return response
    messages.append(message)
    for call in tool_calls:
        function_name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"] or "{}")
        content = "工具调用失败" if function_name != "get_weather" else get_weather(args.get("address", ""))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": content,
            }
        )
    # 递归调用
    return handle_tools(messages, post_request(
        f"{base_url}/chat/completions",
        {"model": model_name, "messages": messages, "tools": TOOLS},
    ))


class MessageIn(BaseModel):
    message: str
    context_id: str

# 非流式输出
@app.post("/chat")
def chat(body: MessageIn):
    logger.info(f"receive_message: {body.message}")
    # 历史消息组装
    messages = context_list[body.context_id]["messages"]
    messages.append({"role": "user", "content": body.message})
    response = post_request(
        f"{base_url}/chat/completions",
        {"model": model_name, "messages": messages, "tools": TOOLS},
    )
    logger.info("response: %s", json.dumps(response, ensure_ascii=False, indent=2))
    # 解析工具调用
    return handle_tools(messages, response)


def event_stream(messages):
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model_name, "messages": messages, "tools": TOOLS, "stream": True},
        stream=True,
        timeout=120,
    )
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
    messages = context_list[body.context_id]["messages"]
    messages.append({"role": "user", "content": body.message})

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
                content = "工具调用失败" if function_call["name"] != "get_weather" else get_weather(json.loads(function_call["arguments"]).get("address", ""))
                messages.append({
                    "role": "tool",
                    "tool_call_id": function_call["id"],
                    "content": content,
                })
        # 单次调用的结束，进入下一轮推理

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 8000
    logger.info(f"Server is running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
