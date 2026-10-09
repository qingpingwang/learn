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
