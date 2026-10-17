from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv
load_dotenv()
import os
import logging
import uuid
from tracing import Agent, langfuse

logging.basicConfig(level=logging.INFO, filename="./log/lesson11.log")
logger = logging.getLogger(__name__)

print("langfuse auth:", langfuse().auth_check(), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # 退出前把还没发出去的 trace 推给 langfuse
    langfuse().flush()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

context_list = {}


def create_context():
    context_id = str(uuid.uuid4())
    context_list[context_id] = Agent(context_id)
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


class MessageIn(BaseModel):
    message: str
    context_id: str


@app.post("/chat")
def chat_api(body: MessageIn):
    logger.info("receive_message: %s", body.message)
    agent = get_context(body.context_id)
    if agent is None:
        return {"error": "context not found"}
    return agent.chat(body.message)


@app.post("/stream_chat")
def chat_stream_api(body: MessageIn):
    logger.info("receive_message: %s", body.message)
    agent = get_context(body.context_id)
    if agent is None:
        return {"error": "context not found"}
    return StreamingResponse(agent.chat_stream(body.message), media_type="text/event-stream")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    logger.info("Server is running on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)
