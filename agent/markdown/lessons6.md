### 多agent协作

上一章把 skill 的正文留到用的时候再加载。skill 一多，调度的人还是看得到全部名称，也还是会自己上手。这里换成主从：父代理只负责派活，活由子代理在自己的上下文里做完，把回复交回来。

子代理之间不直接通信。需要配合时，父代理把上一个的回复写进下一个任务。简单任务这样拆，子代理拿到的是一份干净上下文，注意力更集中，token 也更省。

再往复杂里做，问题就多了：子代理要不要继承用户的全量上下文？生命周期怎么定？要不要异步执行、异步等待？我们这里只做最简单的一种：父代理一次派一个任务，等到结果回来，再进入下一轮 ReAct。

### 子代理目录

和 skill 一样，每轮只放名称和描述。正文、工具、附件都在子代理自己那边，父代理看不到。

```python
SUBAGENTS = {
    "send-email": {
        "description": "起草、确认邮件。用户要发邮件、写邮件、回复邮件时派给它。",
        "system_prompt": "你是邮件子代理，只负责起草邮件。按 skill 的步骤执行。缺信息就列出要问用户的问题，不要编造，也不要整理会议纪要。",
        "skills": ["send-email"],
    },
    "meeting-notes": {
        "description": "把零散的会议记录整理成纪要和待办。用户给出会议内容或要求整理纪要时派给它。",
        "system_prompt": "你是纪要子代理，只负责整理会议纪要。按 skill 的步骤执行。没有原文就说明需要原文，不要起草邮件。",
        "skills": ["meeting-notes"],
    },
}
```

两个子代理的工具集互不相交。`send-email` 只能加载发邮件的 skill，`meeting-notes` 只能加载纪要。谁也没有 `call_subagent`，所以派不出下一层。

父代理的动态提示词里只有这份目录：

```python
def parent_prompt(agent, system_prompt: str) -> str:
    catalog = "\n".join(f"- {name}: {spec['description']}" for name, spec in SUBAGENTS.items())
    return (
        f"{system_prompt}当前的系统时间是：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。\n\n"
        f"可派发的子代理：\n{catalog}"
    )
```

父代理自己的提示词写明它不干活：

```python
SYSTEM_PROMPT = "你是调度助手，你叫‘测试助手’，用中文回答。你不直接写邮件，也不直接整理纪要，只把任务派给子代理，再把结果转述给用户。"
```

### 任务下发

通信只有一条。父代理调用 `call_subagent`，任务字符串下去；子代理的最终回复作为工具结果回来。子代理不进用户会话表，这次调用结束就丢掉。

子代理看不到父代理的对话，所以已知事实必须写在 `task` 里。

```python
def call_subagent(args):
    name = args.get("name", "")
    task = args.get("task", "")
    spec = SUBAGENTS.get(name)
    if spec is None:
        return f"没有这个子代理，可用的有：{', '.join(SUBAGENTS)}"
    allowed = {key: SKILLS[key] for key in spec["skills"] if key in SKILLS}
    child = Agent(
        context_id="",
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        max_tokens=max_tokens,
        system_prompt=spec["system_prompt"],
        dynamic_system_prompt=skill_prompt(allowed),
        tools=SKILL_TOOLS,
    )
    child.tool_handlers["load_skill"] = lambda a: load_skill(child, a, allowed)
    child.tool_handlers["read_skill_file"] = lambda a: read_skill_file(a, allowed)
    logger.info("下发 %s: %s", name, task)
    response = child.chat(task)
    result = response["choices"][0]["message"].get("content") or ""
    logger.info("完成 %s", name)
    return result
```

`load_skill` 和 `read_skill_file` 用的是 `allowed`，不是全部 skills。邮件子代理去加载纪要，会得到「没有这个 skill」。

日志里记两行就够了：下发的时候记任务，回来的时候记完成。过程不用再做一张任务表，父代理的这条工具结果就是过程。

### 调用结果

让它给小王写一封督促学习的邮件。父代理没有自己起草，而是把已知事实装进任务，派给 `send-email`：

```text
> 你好，帮我发邮件给小王，内容是督促他学习。
好的，我把这件事交给发邮件子代理处理。
call_00_uuoTN3OnN6VFg7x38ioX1936 call_subagent {"name": "send-email", "task": "用户（测试助手转述）要求：给\"小王\"发一封邮件，内容是督促他学习。\n\n已知事实：\n- 收件人称呼：小王（用户只提供了称呼，未提供邮箱地址）\n- 邮件目的：督促小王学习\n- 当前系统时间：2026-10-12 16:31:05\n\n需要你完成：\n1. 起草这封督促学习的邮件。\n2. 由于缺少收件人邮箱地址，以及发件人身份、具体学习目标/截止时间等信息，请先向用户确认这些必要信息，不要擅自编造邮箱地址。\n3. 确认后再完成发送，并把最终结果反馈回来。"}
```

子代理的回复作为这次工具的结果回来。它按 skill 加载了模板，缺的信息用占位符留着，没有编邮箱，也没有说已经发出：

```text
result: call_00_uuoTN3OnN6VFg7x38ioX1936 call_subagent ... 我已按 skill 流程加载了模板。以下是草稿，但有几个必要信息缺失，需要你先确认，我不会擅自编造。

待确认信息：
1. 收件人邮箱地址
2. 发件人身份/署名
3. 具体学习目标
4. 截止时间/节点
5. 期望回复时间
6. 是否需要抄送其他人

草稿（占位信息待补充）：
收件人：【待确认：小王的邮箱地址】
主题：关于学习进度的提醒与督促
正文：小王，你好：……麻烦你在【待确认：期望回复时间】前回复我一下你的学习计划和当前进度。
落款：【待确认：落款/发件人】

当前状态：草稿已生成，未发送。
```

父代理没有改这封邮件，只是转述给用户：

```text
我问了负责邮件的小助手，草稿已经拟好了，但有几项必要信息缺失，还没发送（缺邮箱地址也没法发）。需要你补充：

1. 小王的邮箱地址
2. 发件人身份/署名
3. 具体学习内容
4. 截止时间/节点
5. 希望你什么时候收到回复
6. 是否需要抄送其他人

你把上面几项补齐（至少邮箱、学习内容、期望回复时间、落款），我就让邮件小助手出终稿并帮你发送。
```

子代理要问用户的话，只能先交回父代理，由父代理开口。下一轮用户补上邮箱和学习内容之后，父代理再把这些事实写进一次新的任务。上一个子代理的对话已经不在了。

到这里，agent开发的基本流程就已经完成了，剩下的都是些工程化的东西。就像之前提到的，Agent是对人思考问题的一种抽象，出现问题->思考人是如何解决的->优化->出现新问题。在后续的章节中，我们就不在从底层手搓实现了，开始借助优秀的开源框架，专注于解决实际开发中的问题。

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）
