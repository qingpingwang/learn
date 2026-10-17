### codex sdk：技能、沙箱里的 shell

上一章的 dsh，是 DeepSeek 自家的 harness。这一章看 OpenAI 的：Codex SDK（[文档](https://developers.openai.com/codex/sdk)）。官网对它的定位是「用代码去控制本地的 Codex agent」。

它有两个版本：TypeScript 的 `@openai/codex-sdk`，和 Python 的 `openai-codex`。我们一直用 Python，这一课就用后者（写这篇时是 `0.163.0`）。deepseek 的 API 原生支持 Codex 用的 Responses 协议（[文档](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)），所以全程还是 `.env` 里那把 deepseek 的 key。

这一课看三件事：技能能不能被 Codex 自己发现、由模型自己选；shell 能不能关；虚拟文件系统支不支持。

### Python SDK 到底做了什么

和 dsh 一样的套路。官方文档原话：「The Python SDK controls the local Codex app-server over JSON-RPC」。`CodexClient` 用 `subprocess.Popen` 启动 `codex app-server`，通过标准输入输出发 JSON-RPC。`codex` 来自 `openai-codex-cli-bin`。

和 dsh 的差别：dsh 一个会话一个子进程；codex 一个 app-server 管很多条 thread，每条 thread 自己指定 `cwd`。

### 让它用 deepseek

配置写在独立的 `CODEX_HOME`（`src/lesson10/data/codex_home`），不碰本机 `~/.codex`。关键几行：

```toml
model_provider = "deepseek"
wire_api = "responses"          # 只能 responses，chat 已经去掉了
env_key = "DEEPSEEK_API_KEY"    # key 只走子进程环境变量，不落盘
```

### 技能：Codex 自己发现，模型自己选

Codex 的技能也是一个目录加一份 `SKILL.md`，不用写配置，它自己会去几个固定的地方找：`CODEX_HOME/skills` 下面（用户级），以及 thread 的 `cwd` 一路往上、每一层的 `.agents/skills`（项目级）。所以 memo 放在 `src/lesson10/.agents/skills/memo/SKILL.md`，每个会话的工作目录都在 `src/lesson10` 下面，开 thread 时就能扫到它。

注意 `[[skills.config]]` 不是用来加技能的，它只能给已经发现的技能开关。

Codex 只把技能的名字、描述和 `SKILL.md` 的路径放进提示词，正文要模型自己去读，读的办法就是 shell（`cat SKILL.md`）。系统提示里只写了一句通用的话：「用户的请求和某个技能对得上时，先读那个技能的 SKILL.md，再照着做」，没有点名 memo。

### shell：关不掉，只能关进沙箱

第八、九课都把 bash 关了，这一课做不到：Codex 读技能文件靠的就是 shell，关了 shell，模型能看到有 memo 这个技能，却读不到它的正文，只能自己编格式。

所以 shell 开着，靠 thread 的两个参数收住：

```python
self.thread = codex.thread_start(
    cwd=str(self.cwd),
    sandbox=Sandbox.workspace_write,      # 只能写会话工作目录
    approval_mode=ApprovalMode.deny_all,  # 要越权的操作一律拒绝，不弹审批
    developer_instructions=SYSTEM,
)
```

`workspace_write` 管的是写：命令和 `apply_patch` 只能改会话工作目录里的文件。读不受限制，模型能 `cat` 到工作目录外面的 `SKILL.md`，就是这个原因。

### 虚拟文件系统：不支持

**Codex SDK 不支持虚拟文件系统**：Codex 自己的 `apply_patch` / shell 直接读写本地磁盘，SDK 没有接口可以接管。

### 自定义工具

Python SDK 里没有不经过 MCP 的自定义工具接口，所以这一课不挂工具。

### 调用结果

`PORT=8010` 下 `python test/test_lesson10.py --demo` 的实录：

```text
> 在当前目录写一个叫 note.txt 的文件，内容只有一行：hello-from-lesson10
… apply_patch {"paths": [".../workspace/note.txt"]}
… shell {"command": "/bin/zsh -lc 'cat note.txt'"}
result: exit=0 hello-from-lesson10
文件已创建：note.txt，内容为一行 hello-from-lesson10。

> 把刚才的 note.txt 读出来，把原文回给我
… shell {"command": "/bin/zsh -lc 'cat note.txt'"}
note.txt 的原文：hello-from-lesson10

> 帮我记一条备忘：周五下午三点开会
我按 memo 技能的格式来记录这条备忘，先读一下它的说明。
… shell {"command": "/bin/zsh -lc 'cat .agents/skills/memo/SKILL.md'"}
result: exit=1 No such file or directory
… shell {"command": "/bin/zsh -lc 'cat /…/src/lesson10/.agents/skills/memo/SKILL.md'"}
result: exit=0 ---
name: memo
…
… apply_patch {"paths": [".../workspace/memos/friday-meeting.md"]}
… shell {"command": "/bin/zsh -lc 'cat memos/friday-meeting.md'"}
备忘已记录：memos/friday-meeting.md
# MEMO: 周五下午三点开会

周五下午三点开会

-- saved by memo skill
```

几点可以对上号：

- 备忘那一问，没人点名 memo，是模型自己选的技能，自己用 shell 读了 `SKILL.md`（第一次用相对路径没找到，换成提示词里给的完整路径）；
- 写文件都是 `apply_patch`，读文件都是 shell；
- 会话目录里有 `note.txt` 和 `memos/friday-meeting.md`，后者末尾是 `-- saved by memo skill`。

### 测试运行

```sh
cd learn/agent
PORT=8010 sh run.sh lesson10
LESSON_URL=http://127.0.0.1:8010 python test/test_lesson10.py --demo
```

依赖：`openai-codex==0.163.0`。跑完自己确认：

- `cat src/lesson10/data/sessions/*/workspace/note.txt` 内容是 `hello-from-lesson10`；
- `grep 'saved by memo skill' src/lesson10/data/sessions/*/workspace/memos/*.md` 能命中；
- `sh stop.sh lesson10` 停掉服务。

### 小结

- 技能放进 `.agents/skills` 就能被发现，模型自己选、自己读；
- shell 关不掉，只能用 `workspace_write` 沙箱和 `deny_all` 审批把写操作关在会话目录里；
- 不支持虚拟文件系统，文件就是本地磁盘上的文件；
- 所以 Codex SDK 适合在本地应用中嵌入 Codex 的能力，比如给自己的桌面工具或脚本加一个能读写本地项目的 agent。

下一课用 langfuse 给 agent 加上追踪和回归测试。

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）
