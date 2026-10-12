from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
# 加载env文件
from dotenv import load_dotenv
load_dotenv()
import os
import json
import logging
import math
import uuid
from datetime import datetime
import requests
from agent import Agent
# 打印到根目录的log文件
logging.basicConfig(level=logging.INFO, filename="./log/lesson4.log")
logger = logging.getLogger(__name__)

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model_name = os.getenv("MODEL_NAME")
max_tokens = int(os.getenv("MAX_TOKENS", 256))

def dynamic_system_prompt(system_prompt: str) -> str:
    return f"{system_prompt}当前的系统时间是：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。"

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
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "检索小说《斗破苍穹》第一章的内容。用户问到这本书、萧炎或第一章情节时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索问题，例如萧炎为什么变成废材",
                    },
                    "global_search": {
                        "type": "boolean",
                        "description": "问整章讲了什么时为 true，查具体情节时为 false",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

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

def post_completions(base_url, api_key, body, stream=False):
    return requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        stream=stream,
        timeout=120,
    )

def cutoff_index(messages, keep):
    # 保留最后 keep 条，切口不能落在 tool 结果上，要退到这组 tool_call 的开头
    if len(messages) <= keep:
        return 0
    cutoff = len(messages) - keep
    while cutoff > 0 and messages[cutoff].get("role") == "tool":
        cutoff -= 1
    return cutoff

def summary_context(messages):
    # 单独组一份请求，不走中间件，避免压缩时再次进入压缩
    summary_prompt = "把下面的对话压缩成一段简短摘要，保留用户目标、已确认的事实和未完成事项。只输出摘要。"
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": f"{summary_prompt}\n{json.dumps(messages, ensure_ascii=False)}"}],
    }
    response = post_completions(base_url, api_key, body).json()
    return response["choices"][0]["message"].get("content") or ""

def compress_context(agent, system_prompt: str):
    messages = agent.messages
    tokens = estimate_tokens([{"role": "system", "content": system_prompt}, *messages])
    logger.info(f"预估token: {tokens}，max_tokens: {agent.max_tokens}")
    if tokens < agent.max_tokens:
        return
    cutoff = cutoff_index(messages, 5)
    if cutoff <= 0:
        return
    logger.info("触发上下文压缩！")
    summary = summary_context(messages[:cutoff])
    logger.info(f"总结: {summary}")
    # 切口之前换成摘要，切口之后原样留下
    messages[:] = [{"role": "user", "content": f"此前对话摘要：{summary}"}, *messages[cutoff:]]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 上下文管理 id->Agent
context_list = {}

def create_context():
    context_id = str(uuid.uuid4())
    agent = Agent(
        context_id=context_id,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        max_tokens=max_tokens,
        system_prompt=SYSTEM_PROMPT,
        dynamic_system_prompt=dynamic_system_prompt,
        tools=TOOLS,
    )
    agent.add_middleware(compress_context)
    context_list[context_id] = agent
    return {"context_id": context_id}

def delete_context(context_id: str):
    if context_id not in context_list:
        return {"error": "context not found"}
    del context_list[context_id]
    return {"message": "context deleted"}

def get_context(context_id: str):
    return context_list.get(context_id)

@app.post("/create_context")
def create_context_api():
    return create_context().get("context_id")

@app.post("/delete_context")
def delete_context_api(context_id: str):
    return delete_context(context_id)

# 消息输入
class MessageIn(BaseModel):
    message: str
    context_id: str

# 非流式输出
@app.post("/chat")
def chat_api(body: MessageIn):
    logger.info(f"receive_message: {body.message}")
    agent = get_context(body.context_id)
    if agent is None:
        return {"error": "context not found"}
    response = agent.chat(body.message)
    logger.info("response: %s", json.dumps(response, ensure_ascii=False, indent=2))
    return response


@app.post("/stream_chat")
def chat_stream_api(body: MessageIn):
    logger.info(f"receive_message: {body.message}")
    agent = get_context(body.context_id)
    if agent is None:
        return {"error": "context not found"}
    return StreamingResponse(agent.chat_stream(body.message), media_type="text/event-stream")

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 8000
    logger.info(f"Server is running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
