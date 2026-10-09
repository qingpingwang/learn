import json
import urllib.request

url = "http://0.0.0.0:8000/message"


def post_message(msg: str) -> str:
    data = json.dumps({"message": msg}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


if __name__ == "__main__":
    ret = post_message("之前对话我问了你什么？")
    print(ret)
