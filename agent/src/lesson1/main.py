from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
# 加载env文件
import os
from dotenv import load_dotenv
load_dotenv()
import requests
import logging
# 打印到根目录的log文件
logging.basicConfig(level=logging.INFO, filename="./log/lesson1.log")
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


def post_request(url, payload):
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

@app.post("/message")
def receive_message(body: MessageIn):
    history = [
        {"role": "user", "content": "现在几点？"},
        {"role": "assistant", "content": "我不知道"},
        {"role": "user", "content": body.message},
    ]
    response_openai = post_request(
        f"{base_url}/chat/completions",
        {
            "model": model_name,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history],
        },
    )
    response_anthropic = post_request(
        f"{base_url}/anthropic/v1/messages",
        {
            "model": model_name,
            "system": SYSTEM_PROMPT,
            "messages": history,
        },
    )
    return {
        "response_openai": response_openai,
        "response_anthropic": response_anthropic
    }


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 8000
    logger.info(f"Server is running on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
