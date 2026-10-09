from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
# 加载env文件
import os
from dotenv import load_dotenv
load_dotenv()
import json
import requests
import logging
# 打印到根目录的log文件
logging.basicConfig(level=logging.INFO, filename="./log/lesson2.log")
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

class MessageIn(BaseModel):
    message: str


@app.get("/health")
def health_check():
    return {"status": "healthy", "message": "API is running"}


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


@app.post("/message")
def receive_message(body: MessageIn):
    logger.info(f"receive_message: {body.message}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": body.message},
    ]
    response = post_request(
        f"{base_url}/chat/completions",
        {"model": model_name, "messages": messages, "tools": TOOLS},
    )
    logger.info("response: %s", json.dumps(response, ensure_ascii=False, indent=2))
    return handle_tools(messages, response)


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 8000
    logger.info(f"Server is running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
