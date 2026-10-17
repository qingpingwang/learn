import json
import os
import sys
import urllib.request

base = os.getenv("LESSON_URL", "http://127.0.0.1:8000")


def post(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def stream_chat(context_id, message):
    data = json.dumps({"context_id": context_id, "message": message}).encode()
    req = urllib.request.Request(
        base + "/stream_chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: "):
                continue
            chunk = json.loads(line[6:])
            for choice in chunk["choices"]:
                content = (choice.get("delta") or {}).get("content")
                if content:
                    print(content, end="", flush=True)
                tool_calls = choice.get("delta", {}).get("tool_calls", [])
                for tool_call in tool_calls:
                    tool_call_type = tool_call.get("type", "")
                    if tool_call_type == "function_result":
                        print("\nresult:", tool_call["id"], tool_call["function"]["name"], tool_call["function"]["arguments"], tool_call["content"])
                    else:
                        print(tool_call.get("id", ""), tool_call["function"].get("name", ""), tool_call["function"].get("arguments", ""), end="", flush=True)
    print()


DEMO = [
    "在 /workspace/note.txt 写一行：hello-from-lesson11",
    "把 /workspace/note.txt 读出来，原文回给我",
    "帮我把这段会议记录整理成纪要：周五下午三点开会，小王负责发通知，小李周四前准备材料",
]


def lines():
    if "--demo" in sys.argv:
        for text in DEMO:
            print("> " + text)
            yield text
        return
    while True:
        print("> ", end="", flush=True)
        line = sys.stdin.readline()
        if not line or not line.strip():
            return
        yield line.strip()


def main():
    context_id = post("/create_context")
    for text in lines():
        stream_chat(context_id, text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
