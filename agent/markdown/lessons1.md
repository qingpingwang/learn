### api调用-聊一聊llm对话的底层协议

在llm刚出来的时候，各大模型厂商对于对话的底层协议的定义并不相同。这一章以这次实际调用的 deepseek-flash 为例，只看底层：请求打到哪、body 怎么组装、返回怎么拆。对比的是两套主流格式：openai 和 anthropic。

### 调用地址

这部分都可以在 deepseek 官网查看到。

openai：

```python
f"{base_url}/chat/completions"
```

anthropic：

```python
f"{base_url}/anthropic/v1/messages"
```

`base_url` 都是 `https://api.deepseek.com`。两家差在路径，不在主机名。

### 请求体

这次测的是一轮 mock 过的多轮对话。历史固定成两句：用户问「现在几点？」，助手回「我不知道」，然后再接上当前这句用户消息。

两边正常对话的 `role` 都是 `user` / `assistant`。不一样的只有系统提示词放哪。

openai 把系统提示词当成第一条消息：

```json
{
  "model": "deepseek-flash",
  "messages": [
    {"role": "system", "content": "你是一个简洁的助手，用中文回答。"},
    {"role": "user", "content": "现在几点？"},
    {"role": "assistant", "content": "我不知道"},
    {"role": "user", "content": "之前对话我问了你什么？"}
  ]
}
```

anthropic 不收 `role: system`。系统提示词单独放在顶层的 `system`，`messages` 里只留对话：

```json
{
  "model": "deepseek-flash",
  "system": "你是一个简洁的助手，用中文回答。",
  "messages": [
    {"role": "user", "content": "现在几点？"},
    {"role": "assistant", "content": "我不知道"},
    {"role": "user", "content": "之前对话我问了你什么？"}
  ]
}
```

请求代码：

```python
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
        "response_anthropic": response_anthropic,
    }
```



### openai的返回

默认都带了深度思考。

```json
{
  "id": "cf2c6f83-b2d2-40aa-a73b-05ebf3160e9d",
  "object": "chat.completion",
  "created": 1791512514,
  "model": "deepseek-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你之前问的是：“现在几点？”",
        "reasoning_content": "我们需要回答用户中文问题：“之前对话我问了你什么？”我们需要知道之前对话。在当前对话中，用户先问“现在几点？” 我回答“我不知道”。然后用户问“之前对话我问了你什么？” 所以之前对话中用户问的是“现在几点？” 我们需要简洁中文回答。可以指出：你之前问的是“现在几点？”。注意不要声称有记忆，只是基于当前对话。直接回答。"
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 54,
    "completion_tokens": 97,
    "total_tokens": 151,
    "prompt_tokens_details": {
      "cached_tokens": 0
    },
    "completion_tokens_details": {
      "reasoning_tokens": 88
    },
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 54
  },
  "system_fingerprint": "aeb56401ca74e127821c4f9126dcb669"
}
```

这里主要关注这几部分：

- `id`：此轮对话的 id。
- `model`：这次实际调用的模型 id。
- `choices`：返回消息放在这里。`message.content` 是回复正文，`message.reasoning_content` 是推理过程，`finish_reason` 是停止原因。`stop` 就是正常说完。
- `usage`：这次调用的 token 消耗。

```text
prompt_tokens：发出去的内容，加上接口自己带的固定开销。这次是 54。
completion_tokens：模型生成的全部 token，包含内部推理和最终回复。这次是 97。
total_tokens：两者相加，54 + 97 = 151。账单按这个总量算。
reasoning_tokens：推理消耗，这次是 88。97 里剩下的 9 是回复正文。
cached_tokens / prompt_cache_hit_tokens：输入里命中缓存的 token。这次是 0。
prompt_cache_miss_tokens：没命中、重新计算的输入 token。这次等于全部 prompt_tokens。
命中缓存时，总的 token 消耗会下降，这是 llm 底层的一种优化策略。
```



### anthropic的返回

```json
{
  "id": "cc07d5c8-0ab4-430d-a024-98ae8eedb1cf",
  "type": "message",
  "role": "assistant",
  "model": "deepseek-flash",
  "content": [
    {
      "type": "thinking",
      "thinking": "我们需要回答用户中文问题。需要处理。用户问“之前对话我问了你什么？”我们没有之前对话上下文，因为这是新会话？系统没提供历史。需要诚实说明我看不到之前对话。但要注意，刚才用户问“现在几点？”我回答“我不知道”。这算当前对话中之前的问题？用户问“之前对话我问了你什么？”可以理解为在这段对话里之前问了什么。当前对话中用户之前只问了“现在几点？”。所以可以回答：在这段对话里，你之前问的是“现在几点？” 如果是更早的历史对话，我这边没有记录/看不到。需要简洁。用中文。要说明无法访问之前会话。可以同时回答当前可见的。最终简洁。",
      "signature": "cc07d5c8-0ab4-430d-a024-98ae8eedb1cf"
    },
    {
      "type": "text",
      "text": "在这段对话里，你之前问的是：「现在几点？」\n\n如果你指的是更早的历史对话，我这边看不到，无法确认。"
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 54,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 177,
    "service_tier": "standard"
  }
}
```

协议格式和 openai 差距很大，要看的东西还是那几样：id、模型、角色、停止原因、回复、推理、token。

拆开看：

- 没有 `choices`。整条返回自己就是这条 assistant 消息，`role` 在最外层。
- 回复不在 `message.content` 一个字符串里，而在 `content` 数组里。推理和正文平级，用 `type` 区分：`thinking` 是推理，`text` 是正文。
- 停止原因叫 `stop_reason`。`end_turn` 就是正常说完，对应 openai 的 `finish_reason: stop`。
- `usage` 仍在最外层，但字段名换了：`input_tokens` 对 `prompt_tokens`，`output_tokens` 对 `completion_tokens`。这里没有单独的推理 token 计数，推理和正文都算在 `output_tokens` 里，只能自己按 `thinking` 和 `text` 的长度去估。
- 缓存命中看 `cache_read_input_tokens`。这次是 0。



### 两边对照


| 要找的东西    | openai                                             | anthropic                                 |
| -------- | -------------------------------------------------- | ----------------------------------------- |
| 地址       | `/chat/completions`                                | `/anthropic/v1/messages`                  |
| 系统提示词    | `messages` 第一条，`role: system`                      | 顶层字段 `system`                             |
| 对话历史     | `messages` 里的 `user` / `assistant`                 | 同样，只是没有 system 那条                         |
| 回复正文     | `choices[0].message.content`                       | `content` 里 `type: text` 的 `text`         |
| 推理过程     | `choices[0].message.reasoning_content`             | `content` 里 `type: thinking` 的 `thinking` |
| 停止原因     | `choices[0].finish_reason`                         | `stop_reason`                             |
| 输入 token | `usage.prompt_tokens`                              | `usage.input_tokens`                      |
| 输出 token | `usage.completion_tokens`                          | `usage.output_tokens`                     |
| 推理 token | `usage.completion_tokens_details.reasoning_tokens` | 没有单独字段                                    |


仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）