### function-calling的使用-agent工作的基础

前一章分别用 openai / anthropic 两套格式调用了一次底层 llm。后面的开发就固定用 openai 的格式。

这一章算是大模型发展史上的一次里程碑：function-call 出现了。在这之前，大模型只能推理、输出，并不能帮用户操作任何事情。写代码、操作浏览器、整理文件都做不了。这时候的 llm 还是一个没有肢体的超级大脑。

function-call 出现之后，llm 才和宿主的机器、代码产生关联。它可以调用本地的命令行和代码，去感知用户机器的状态，然后再思考、执行对应的函数（react模式）。

最早期不是所有 llm 都支持 function-call。现在这已经是大语言模型的基本能力了。不得不感叹，AI 的发展之快。

本章以查询天气为例，让 deepseek 调用天气查询，回复用户有关天气的问题。

### 工具怎么声明

openai 的官方协议格式可以在这里查：[https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

我们定义的 tools 如下：

```python
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
```

对应的执行函数就简单点：

```python
def get_weather(address):
    return f"{address}是晴天!"
```

带上工具再调用：

```python
response = post_request(
    f"{base_url}/chat/completions",
    {"model": model_name, "messages": messages, "tools": TOOLS},
)
```

由于我们并没有接入类似FastMCP这样的库，帮我们去处理schema、调用。所以在调用完成后，我们需要自己去
解析结果，查看是否触发了`tool_calls`。

### 模型发起工具调用

以提问「今天杭州天气怎么样？」为例，追踪这次返回：

```json
{
  "id": "684a9b1f-a7a0-4853-b80f-80f97caf23e6",
  "object": "chat.completion",
  "created": 1791521467,
  "model": "deepseek-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "",
        "reasoning_content": "The user asks about Hangzhou weather today. I should call the weather tool.",
        "tool_calls": [
          {
            "index": 0,
            "id": "call_00_q4pSstRvTU14wNBWBER22266",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"address\": \"杭州\"}"
            }
          }
        ]
      },
      "logprobs": null,
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 316,
    "completion_tokens": 55,
    "total_tokens": 371,
    "prompt_tokens_details": {
      "cached_tokens": 128
    },
    "completion_tokens_details": {
      "reasoning_tokens": 16
    },
    "prompt_cache_hit_tokens": 128,
    "prompt_cache_miss_tokens": 188
  },
  "system_fingerprint": "aeb56401ca74e127821c4f9126dcb669"
}
```

可以看到，基本的消息格式和前一章是一样的。不过这里停止推理的理由是：`tool_calls`
我们可以根据这个字段，或者直接解析`tool_calls`，循环将要对应的工具结果返回。注意：这里不能漏掉、或是跳过，对于llm来说，未拿到对应的`tools_result`则会被认为是调用错误。

### 返回工具调用结果

这块的代码belike：

```python
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
```

这一块需要注意的是：调用工具返回后，进入大模型的`react`，推理出来的结果可能还需要继续工具调用（这种场景很常见，比如先查询完地址，再根据地址查询天气等）。所以这里处理工具调用要递归处理，直到llm认为不需要调用工具为止。这里可能大模型会抽风（早期），所以为了安全起见，可以设置一个最大递归深度，防止出现死循环。  
由于我们只有get_weather函数，所以只执行这个（TOOLS告知了tool_list的schema，理论上不会调用额外的工具，这里只是安全性校验）。

工具调用到这里就结束了，但是工程上，有很多很多事情要去做，比如工具的权限校验（`human in the loop`），需要做的事情还非常多。

工具结果送回去之后，模型这次不再调用工具，直接给出最终回复：

```json
{
  "id": "d25e81d5-c445-4e66-9fd6-59f85d745f9f",
  "object": "chat.completion",
  "created": 1791522161,
  "model": "deepseek-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "今天杭州是晴天 ☀️，适合外出活动。",
        "reasoning_content": ""
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 386,
    "completion_tokens": 13,
    "total_tokens": 399,
    "prompt_tokens_details": {
      "cached_tokens": 128
    },
    "completion_tokens_details": {
      "reasoning_tokens": 0
    },
    "prompt_cache_hit_tokens": 128,
    "prompt_cache_miss_tokens": 258
  },
  "system_fingerprint": "aeb56401ca74e127821c4f9126dcb669"
}
```

如果接入mcp的话，就能帮助我们处理schema的生成和函数的调用，以及外接mcp-server，但这不是我们章节的主要内容了。

有了function-call，便能让llm帮助你写代码、做ppt、操作电脑甚至是其它硬件设备，给了llm更多的想象空间，从此agent开发才真正进入百花齐放的时代。

在下一章节，我们将此工程改在成一个能agent-loop、维护上下文、支持流式聊天的最小agent项目

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）