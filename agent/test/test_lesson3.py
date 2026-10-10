import json
import sys
import urllib.request

base = "http://127.0.0.1:8000"


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


def chat(context_id, message):
    body = post("/chat", {"context_id": context_id, "message": message})
    print(body["choices"][0]["message"].get("content") or "")


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
                # 输出工具调用
                tool_calls = choice.get("delta", {}).get("tool_calls", [])
                for tool_call in tool_calls:
                    tool_call_type = tool_call.get("type", "")
                    if tool_call_type == "function_result":
                        print("\nresult:", tool_call["id"], tool_call["function"]["name"], tool_call["function"]["arguments"], tool_call["content"])
                    else:
                        print(tool_call.get("id", ""), tool_call["function"].get("name", ""), tool_call["function"].get("arguments", ""), end="", flush=True)
    print()


def lines():
    while True:
        print("> ", end="", flush=True)
        line = sys.stdin.readline()
        if not line or not line.strip():
            return
        yield line.strip()


def main():
    context_id = post("/create_context")["context_id"]
    for text in lines():
        stream_chat(context_id, text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
