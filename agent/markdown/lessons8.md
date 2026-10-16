### deepagents：自己的文件系统

这一章，我们使用集成度更高一点的架构：deepagents（[文档](https://langchain-zh.cn/oss/python/deepagents/overview)）。根据官网的内容介绍，我们就能看出它的强大。它本身也是基于langchain家族开发出来的。列一些比较亮眼的集成功能：

1. 包含内置工具，去操作规划和任务拆解；
2. 提供上下文管理的能力（动态将上下文卸载到内存或者文件系统中，以及过程的工具结果调用）；
3. 虚拟文件系统（这对我们做后端服务化部署很重要，我们可以接自己的后端服务来完成虚拟文件系统）；
4. 子代理的支持；
5. 长期记忆（跨会话的记忆）；
6. 智能默认值（很多高质量的系统提示词预设）；

其实看到这里，agent的开发就已经是过去式了。对于头部公司来说，可能需要优化、迭代自己的agent，但是对于大部分公司来说，deepagents本身就是可望也可及的白月光了。

接下来，我们主要体验deepagents的虚拟文件系统+长期记忆。

### deepagents 虚拟文件系统的实现

服务启动时建一个 `VirtualFS`，交给 `create_deep_agent` 的 `backend`。技能和长期记忆写虚拟路径。

```python
FS = VirtualFS(DATA / "sessions", skills=SKILLS, memories=DATA / "memories")

GRAPH = create_deep_agent(
    model=model,
    system_prompt=SYSTEM,
    backend=FS,
    skills=["/skills/"],
    memory=["/memories/AGENTS.md"],
    permissions=PERMISSIONS,
    subagents=[BRIEF],
    checkpointer=InMemorySaver(),
)
```

收到一条消息，先把这次会话绑上，再调用。`thread_id` 用同一个 `context_id`。

```python
fs.bind(context_id)
graph.invoke(
    {"messages": [{"role": "user", "content": msg}]},
    {"configurable": {"thread_id": context_id}},
)
```

模型不碰磁盘，只调 `VirtualFS` 上的方法：

- `ls(path)` → 工具 `ls`
- `read(file_path, offset=0, limit=2000)` → 工具 `read_file`
- `write(file_path, content)` → 工具 `write_file`。超长消息被挪走时也会调 `write`
- `edit(file_path, old_string, new_string, replace_all=False)` → 工具 `edit_file`
- `delete(file_path)` → 工具 `delete`
- `glob(pattern, path=None)` → 工具 `glob`
- `grep(pattern, path=None, glob=None, max_count=None)` → 工具 `grep`

技能和长期记忆不走这些工具。中间件先 `ls` 找出 `/skills` 下的子目录，再用 `download_files` 把 `SKILL.md` 和 `/memories/AGENTS.md` 读进提示词。

异步跑的时候调 `als`、`aread` 这些同名方法。协议里的默认实现是把同步方法丢进线程，这里不另写一份。`execute` 只有带 shell 的后端才有，这个类不实现。`local()` 不在协议里，deepagents 不会调，服务端用它拿本地目录。

这一章里模型实际在做的是：`ls` 看目录，`read_file` 读技能和记忆，`write_file` 把这一轮的稿子放到 `/workspace`，`edit_file` 改 `/memories/AGENTS.md`。换一条会话，`/workspace` 是空的，`/memories` 还在。

工具还是它的 `ls`、`read_file`、`write_file`、`edit_file`。存储换成我们的 `VirtualFS`。

模型看见的路径和文件真正落在哪，是两件事。路径决定这份数据活多久、谁能改。


| 路径            | 本地目录                           | 谁能写             |
| ------------- | ------------------------------ | --------------- |
| `/skills/`    | `src/lesson8/skills`，所有会话共用    | 不能。服务端把测试技能放在这里 |
| `/memories/`  | `data/memories`，所有会话共用         | 能写。换一条会话还在      |
| `/workspace/` | `data/sessions/<会话>/workspace` | 能写。只属于这一次会话     |


`local(path)` 把虚拟路径换回本地目录。deepagents 不调用它，服务端用它知道文件在哪。

```python
fs = VirtualFS(DATA / "sessions", skills=SKILLS, memories=DATA / "memories")
fs.local("/skills")  # .../lesson8/skills
```

每一轮对话先 `bind(context_id)`。`/skills` 和 `/memories` 不动，`/workspace` 落到这个会话自己的目录。

`data` 只放会变的东西。技能源文件在 `src/lesson8/skills`，不进 `data`。

```text
src/lesson8/
├── skills/                          /skills，只读
│   ├── meeting-notes/SKILL.md
│   └── send-email/
│       ├── SKILL.md
│       └── references/template.md
└── data/
    ├── memories/
    │   └── AGENTS.md                /memories，所有会话共用
    └── sessions/
        └── <context_id>/
            └── workspace/           /workspace，只属于这一次会话
```

`context_id` 就是 `/create_context` 返回的那一串。换一条会话，就多一个 `sessions/<context_id>`。压缩掉的原文如果出现，也在这个会话目录下，路径是 `conversation_history`，不会写进 `memories`。

### skills

测试技能沿用第 6 章的两份，放在 `src/lesson8/skills/`：

- `meeting-notes/SKILL.md`
- `send-email/SKILL.md`
- `send-email/references/template.md`

每个 `SKILL.md` 开头仍是 `name` 和 `description`。`name` 必须和目录名一致。创建 agent 时把来源写成 `skills=["/skills/"]`。每轮只把名称和描述放进提示词，步骤留在文件里，要用再 `read_file`。

发邮件那一步不再调用第 6 章的 `read_skill_file`，改成读 `/skills/send-email/references/template.md`。

实际问「有哪些 skill？只列名称，不要执行。」返回：

```text
当前有两个 skill：

- meeting-notes —— 把零散的会议记录整理成纪要
- send-email —— 起草并确认邮件
```

### 子代理

只留一个子代理 `brief`。默认的 general-purpose 关掉，避免再多一个什么都能做的代理。`brief` 不能写 `/skills` 和 `/memories`。它把材料压成 5 行，写入 `/workspace/brief.md`。

问它把「杭州 E 类人才住房补贴要看当年申报通知，材料一般在浙里办提交」压成简报，文件落在当前会话的 `workspace/brief.md`：

```text
1. 主题：杭州E类人才住房补贴的申报。
2. 依据：以当年发布的申报通知为准。
3. 渠道：申报材料一般在“浙里办”提交。
4. 注意：申报要求按年度发布，旧年度信息不可直接套用。
5. 兜底：具体条件、时间与材料以当年官方通知为准。
```

### 长期记忆

`/memories/AGENTS.md` 在创建服务时放一句「还没有需要跨会话记住的事实」。`memory=["/memories/AGENTS.md"]` 会在会话开始时读进提示词。换一条会话还能读到，所以它是跨会话的。主代理可以直接改这个文件。

权限只拦 `/skills`：

```python
PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
]
```

`/workspace` 和 `/memories` 没有这条规则，直接写。`brief` 自己的权限更严，这两处它都不能写。后端仍然拒绝 `/skills`，避免绕过工具直接写进去。

### 调用结果

以追问文件系统相关内容+写入长期记忆为例：

```text
> 你好
你好！我是测试助手，很高兴见到你。

我可以帮你做这些事：

- 整理会议纪要 —— 你把零散的会议记录丢给我，我整理成结构化纪要（含待办）
- 起草/回复邮件 —— 写邮件、回邮件，我会先起草给你确认再发
- 读写文件、搜索文件内容、整理信息等

直接说你想做什么就行。
> 当前系统下有哪些文件？
我来看看。
call_00 ls {"path": "/"}
call_01 ls {"path": "/workspace"}
call_02 ls {"path": "/skills"}
call_03 ls {"path": "/memories"}
result: call_01 ls No files found
result: call_03 ls ['/memories/AGENTS.md']
result: call_02 ls ['/skills/meeting-notes/', '/skills/send-email/']
result: call_00 ls ['/memories/', '/skills/', '/workspace/']
call_00 ls {"path": "/skills/meeting-notes"}
call_01 ls {"path": "/skills/send-email"}
result: call_00 ls ['/skills/meeting-notes/SKILL.md']
result: call_01 ls ['/skills/send-email/SKILL.md', '/skills/send-email/references/']
call_00 ls {"path": "/skills/send-email/references"}
result: call_00 ls ['/skills/send-email/references/template.md']
```

它接着列出这棵树：`/memories/AGENTS.md`、两份 `SKILL.md`、`/skills/send-email/references/template.md`，`/workspace` 是空的。

```text
> 记住：我叫王清平
call_00 read_file {"file_path": "/memories/AGENTS.md"}
result: @@ lines 1-1 of 1 @@
还没有需要跨会话记住的事实。
call_00 edit_file {"file_path": "/memories/AGENTS.md", "old_string": "还没有需要跨会话记住的事实。", "new_string": "- 用户姓名：王清平"}
result: Successfully replaced 1 instance(s) of the string in '/memories/AGENTS.md'
好的，已经记住：你叫王清平。以后需要称呼你或者填署名的地方，我会用这个名字。
```

写完之后，`data/memories/AGENTS.md` 里就是这一句：

```text
- 用户姓名：王清平
```

### 上下文压缩

deepagents 自带压缩。对话变长之后，被挪走的原文写到当前会话的 `/conversation_history/`，对话里留下摘要和这个路径。要原文就 `read_file`。这一轮的稿子仍然放在 `/workspace`，两处不要混。

同一条 `context_id` 接着说，检查点里留着之前的消息。这里不再接第 7 章的工作流。

接口和第 7 章一样：`/create_context`、`/chat`、`/stream_chat`。

```sh
sh run.sh lesson8
python test/test_lesson8.py
```

集成度越来越高的框架，就像rocket mq、redis一样。再往后只需要大厂去研究，小厂改吧改吧甚至是直接就能部署到企业级应用了。agent的开发，又像系统提示词工程师一样，得默默无闻的消失了。其实说到底，智能体的核心还是上下文的治理，并没有变化太多，包括之前的上下文工程、RAG、Wiki都是一样。

  
仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）
