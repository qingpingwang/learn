### dsh：把一个现成的 agent 程序接进服务

这一张，我们用一下国内大名鼎鼎的harness，DeepSeek 官方开源的 dsh（DeepSeek Harness，[仓库](https://github.com/deepseek-ai/deepseek-harness)、[文档](https://deepseek-harness.github.io/deepseek-harness/en/guide/python-sdk)）。

harness 这个词可以理解成「套在大模型外面的那层壳」：管会话、管工具、真正去执行命令、把对话记到磁盘上。官网介绍里比较亮眼的几点：

1. **一切皆插件**：模型适配、工具、会话日志，连 agent 循环本身都是插件；
2. **profile**：一套预先配好的插件组合，`sdk`、`sdk-minimal`、`web` 都是 profile；
3. **patch**：在 profile 上增删插件、改配置，写成一份 yml 文件；
4. **会话自动落盘**：按 `session_id` 存成 JSONL 文件；
5. **Python SDK 不用装 Node**：pip 包里直接带了 `dsh` 可执行文件。

注意：写这篇的时候（2026-10-16）它还是预览版，`deepseek-harness-sdk` 是 `0.1.5rc1`，一个月里发了好几版，下面的代码都锁了版本。目前总体来说还是不太稳定，不太适合做成服务化部署（本地部署还是很合适的）。

### dsh Python SDK

先说结论：Python 这边**不跑 agent**运行时，它只是调用dsh的子进程去操作，所以本质上也是一个黑盒调用。

0.1.5rc1 的源码里，`client.py` 里用 `subprocess.Popen` 启动 `dsh --profile <profile>`（还会带上传的 `--patch`），然后通过标准输入输出发 JSON-RPC 2.0 消息。`dsh` 这个程序来自它依赖的另一个包 `deepseek-harness-runtime-bin`。

所以一个 `DeepSeekHarness` 对象，背后就是一个子进程。这里有三个比较主要的参数：

- `dsh_home`：dsh 自己的家目录，profile、会话 JSONL 都放这。**必须显式传**，SDK 不会去读 `~/.dsh`；
- `cwd`：agent 干活的目录，相对路径默认从这里解析；
- `profile`：用哪套插件组合。

```python
def build_harness(cwd: Path) -> DeepSeekHarness:
    return DeepSeekHarness(
        profile="sdk-minimal",
        patches=(str(PATCH),),            # src/lesson9/patches/lesson9.patch.yml
        dsh_home=str(HOME),
        cwd=str(cwd),
        provider="deepseek-official",
        model=os.getenv("MODEL_NAME"),
        api_key=os.getenv("API_KEY"),     # 只进子进程的 DEEPSEEK_API_KEY
        base_url=os.getenv("BASE_URL"),
        request_timeout_seconds=300,
        runtime_cwd=str(HERE),            # dsh 进程的工作目录：patch 里 config 的相对路径从这里算
    )
```



### profile 和 patch：和第八课对齐的那一套

这一课用的是 `sdk-minimal`。它本身很「瘦」：自带一个常驻 bash、JSONL 会话，**没有** `ctx.fs`，也没有文件工具、没有技能。

我们要的是和第八课一样的组合：**技能 + 虚拟文件系统 + 工具，不给 bash**。这些都靠一份 patch 叠上去：关掉 bash 和会话日志上传，再插入自己的 `fs-virtual`、官方的文件工具和技能插件，以及一个天气工具。

按官方文档，`session-log-deepseek` 默认会把会话日志随请求一起上传给 DeepSeek（每次最多 8 MiB）。不想传，就在 patch 里把这一行关掉。

### 自己写一个 ctx.fs：fs-virtual

dsh 的文件工具（`@deepseek-ai/dsh-tool-fs`）不碰磁盘，它们只调 `ctx.fs` 上那一组方法：`resolve`、`readText`、`writeText`、`editText`、`listDir`……谁提供 `ctx.fs`，文件就落到哪里。

`sdk-minimal` 里本来就没有 fs 这一行，所以不用禁用谁，直接插一个自己的插件。插件放在 `src/lesson9/plugins/fs-virtual.mjs`。单文件运行时里本地插件 import 不到 `@deepseek-ai/dsh-fs`，所以不能继承官方的 `FileSystem` 类，只能写一个**同形状的普通对象**，用 `ctx.provide('fs', …)` 挂上去。方法签名是对照 0.1.5rc1 的抽象类一个一个抄的。

真正的读写不写死在插件里，而是交给一个存储适配器：

- `LocalDiskStorage`：落在本机磁盘。相对路径按文件工具传进来的会话 cwd 解析，只是解析起点，**不是围栏**——这一点和官方 `fs-local` 一样。技能目录在会话目录之外，`skill-filesystem` 会通过 `ctx.fs` 读绝对路径，如果把越出 root 的路径一律拒绝，技能就加载不出来；
- `RemoteApiStorage`：远程后端的占位。类在，方法全抛 `FS_IO_ERROR`，**未实现、未测试**。以后要接对象存储，只要换这一层。

每次读写还会往 `src/lesson9/data/fs-virtual.log` 追加一行，方便核对「模型调的 write/read，到底有没有经过我们的适配器」。

```javascript
export function apply(ctx, config = {}) {
  const storage = (config.backend ?? 'local-disk') === 'remote-api'
    ? new RemoteApiStorage(config.endpoint)
    : new LocalDiskStorage(config.root ?? process.cwd())
  // …
  ctx.provide('fs', new VirtualFileSystem(storage, log))
}
```

有一点要说清楚：官方 `FsError` 是一个特定的类，`tool-fs` 用 `instanceof FsError` 做错误映射。我们这边拿不到那个类，只能自己造一个同名同码的 Error。实测下来，文件不存在时模型仍能看到「not found」这类提示，能继续写；若哪天官方收紧了错误映射，这里可能要再改。

### 技能：三个官方插件 + 一个 memo

技能这条链路要插三个包（运行时二进制里都有，字符串搜得到）：

- `@deepseek-ai/dsh-skill`：技能注册表，提供 `ctx.skills`；
- `@deepseek-ai/dsh-skill-filesystem`：从磁盘目录发现技能。我们关掉默认根目录，只留 `customSkillDirs` 指向 `src/lesson9/skills`；
- `@deepseek-ai/dsh-tool-skill`：给模型一个叫 `skill` 的工具，用来按名字加载技能正文。

技能本身是一个目录，里面有一份 `SKILL.md`，开头是 YAML frontmatter（必填 `name`、`description`），后面是说明书。这一课放了一个 `memo` 技能：用户说「记一条备忘」时，模型应先调 `skill` 加载它，再按固定格式用 `write` 写到 `memos/` 下，最后 `read` 确认。

### 天气工具

天气还是上一版那个 `weather-tool.mjs`：导出 `apply`，往 `ctx.tools` 注册 `get_weather`，数据来自 `wttr.in`。和文件、技能一起塞进同一份 patch。

patch 是一份手写的 yml，放在 `src/lesson9/patches/lesson9.patch.yml`。它是一串操作，从上往下读就行：带 `id` 的是改 profile 里已有的那一行（改配置，或者 `disabled: true` 关掉），`insert` 是插入新行。插件的 `name` 写本地路径或者 npm 包名。路径都是相对的：插件的 `name` 相对这份 yml 文件，`config` 里的路径（技能目录、日志文件）相对 dsh 进程的工作目录，也就是 `build_harness` 里 `runtime_cwd` 指定的 `src/lesson9`。

```yaml
# 第 9 课的 dsh patch：叠在 sdk-minimal 上。
# 一条一条往下读：带 id 的是改 profile 里已有的那一行，insert 是插入新行。

# 1) 系统提示：改 sdk-minimal 里 system-prompt 这一行的配置
- id: system-prompt
  config:
    personaPrefix: 你是测试助手，用中文回答。你没有 shell，只能用 read / write / edit 工具在当前工作目录里读写文件，路径一律用相对路径。用户的请求和某个技能对得上时，先调用 skill 工具加载它，再照着做。查询天气时必须调用 get_weather 工具，不要凭记忆回答，根据工具结果给出简洁的天气概况和穿衣/出行建议。

# 2) 会话日志默认会随请求上传给 DeepSeek，关掉
- id: session-log-deepseek
  disabled: true

# 3) 关掉 bash：和第八课一样，不让模型跑命令
- id: persistent-bash
  disabled: true

# 4) 插入插件
- insert:
  # 自己写的 ctx.fs 提供者（sdk-minimal 本来没有 fs 这一行）
  - id: fs-virtual
    name: ../plugins/fs-virtual.mjs
    config:
      backend: local-disk
      logFile: data/fs-virtual.log

  # read / write / edit 文件工具，底下调用 ctx.fs
  - id: tool-fs
    name: '@deepseek-ai/dsh-tool-fs'

  # 技能：注册表 + 从磁盘目录发现 + 给模型的 skill 工具
  - id: skill
    name: '@deepseek-ai/dsh-skill'
  - id: skill-filesystem
    name: '@deepseek-ai/dsh-skill-filesystem'
    config:
      includeDefaultRoots: false
      customSkillDirs:
        - skills
  - id: tool-skill
    name: '@deepseek-ai/dsh-tool-skill'

  # 天气工具
  - id: demo-weather-tool
    name: ../plugins/weather-tool.mjs
    config:
      endpoint: https://wttr.in
      timeoutMs: 20000
```

Python 这边不用做任何处理，把这个文件的路径交给 `DeepSeekHarness(patches=...)` 就行，SDK 会转成 `--patch <路径>` 传给 dsh：

```python
PATCH = HERE / "patches" / "lesson9.patch.yml"
```

这份 patch 里没有用到 `!!js` 这类标签，都是普通的 yml。

### 一个上下文一个子进程

和前面几课一样，对外还是 `/create_context`、`/stream_chat`。里面每个 `context_id` 对应一个 `Agent`：一个工作目录 + 一个 `DeepSeekHarness`（也就是一个 dsh 子进程）。事件从 `session.event` 进来，翻译成前面几课那种 SSE delta，前端不用改。

同一个服务进程里，同一个 `context_id` 接着问，它记得前面聊过什么。

### 安全

`sdk-minimal` 自带的权限模式是 `danger-full-access`。我们关掉了 bash，模型不能直接跑命令；文件走的是我们的 `fs-virtual`。但 `LocalDiskStorage` 和官方 `fs-local` 一样，**root 不是围栏**——绝对路径照样能读到技能目录。真正要关进笼子，得换沙箱后端，或者自己在适配器里加边界，这一课没做。

### 调用结果

下面是 `PORT=8009` 跑 `python test/test_lesson9.py --demo` 的实录（天气是实时的，每次跑出来的数字会不一样）：

```text
> 用 write 工具在当前目录写一个叫 note.txt 的文件，内容只有一行：hello-from-fs-virtual
… write {"file_path": "note.txt", "content": "hello-from-fs-virtual\n"}
result: … Created file
已创建 note.txt，内容为一行 hello-from-fs-virtual。

> 用 read 工具把刚才的 note.txt 读出来，把原文回给我
… read {"file_path": "note.txt"}
result: … 1: hello-from-fs-virtual
note.txt 的原文：hello-from-fs-virtual

> 帮我记一条备忘：周五下午三点开会
… skill {"name": "memo"}
result: … <skill_content name="memo">…</skill_content>
… write {"file_path": "memos/friday-meeting.md", …}
… read {"file_path": "memos/friday-meeting.md"}
备忘已记好，路径 memos/friday-meeting.md

> 厦门今天天气怎么样？需要带伞吗？
… get_weather {"city": "Xiamen"}
result: … 城市：Gulangyu, China
当前：Partly Cloudy ，23°C（体感 25°C），湿度 84%，西北偏西风 9 km/h，观测时间 11:09 PM
2026-10-17：Partly Cloudy ，22~28°C，降水概率 6%
2026-10-18：Sunny，24~29°C，降水概率 5%
2026-10-19：Sunny，24~29°C，降水概率 4%
建议：不用带伞；湿度偏高，穿短袖即可。
```

几点可以对上号：

- 全程没有 `bash` 调用；
- `src/lesson9/data/fs-virtual.log` 里能看到 `writeText note.txt`、`readText note.txt`，以及技能目录的 `listDir` / `readText SKILL.md`；
- 会话目录里有 `note.txt` 和 `memos/friday-meeting.md`，后者末尾是 `-- saved by memo skill`；
- 天气那一问出现的是 `get_weather`，不是模型凭记忆编的。

### 为什么 dsh 不适合放在服务端

这一课把 dsh 包成了一个 HTTP 服务，跑起来没问题，但真要当线上服务端用，它不太合适。原因主要是两点。

**插件没法放在云上。** patch 里的 `name` 只认两种写法：本地路径，或者已经装好的 npm 包名，不认 URL。所以插件代码必须先落到本地磁盘：要么启动前用 Python 把它下载下来，再把本地路径写进 patch；要么把它发布成 npm 包装好。插件读写的**数据**倒是可以在远端，比如 fs-virtual 换成 `RemoteApiStorage` 就是把文件存到别的服务上。**工具**也能挪到云上：通过 MCP（`dsh-mcp-client` 按 URL 连过去），或者写一个很薄的转发插件。但像 fs-virtual 这种往 `ctx` 里提供服务的插件，只能在本地加载。patch 本身也一样：SDK 的 `patches` 只收本地文件路径，用 `--patch` 交给 dsh，没有直接传内存字符串的选项。

**进程模型有限制。** SDK 每个 harness（也就是每个会话）都要起一个 dsh 子进程，用 subprocess 加 stdio 上的 JSON-RPC 通信。并发多了，就是一堆进程和一堆内存。会话记录存在本地 `dsh_home` 的磁盘上，所以会话没法在机器之间迁移，也就没法横向扩容。skill-filesystem 扫描的也是本地目录。隔离上，我们能做的只是关掉 bash。

对比一下第八课的 deepagents：它是一个库，跑在我们自己的进程里，不用多起子进程；checkpointer 可以换成 Postgres，会话状态放到数据库里，多台机器都能接着跑；VirtualFS 后端连记忆和技能都能一起覆盖。所以 dsh 更适合本地、单机、一人一个进程的场景，比如命令行或桌面助手；要做多用户的服务端，deepagents 这种库形态更顺手。

dsh 给我的感觉是：agent 开发从「自己搭」变成了「拿一个现成的去指挥」。和第八课比，文件系统从「库里的 backend」变成了「patch 插进去的插件」；技能、工具也是同一条插件链。代价是黑盒、版本还在剧烈变化，本地插件也写不成官方的 TypeScript 子类。

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）