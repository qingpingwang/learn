### 对比篇：同一套能力，三家怎么接

第八到第十课，连着用了三个上层框架：deepagents、dsh、codex sdk。这一章不写新代码，只把它们放在同一条尺子上比：**技能、虚拟文件系统、工具，而且不给 bash**。

先用一句话说形态：

- **deepagents** 是进程内的库，agent 跑在你的 Python 里；
- **dsh** 是独立子进程，Python 只是 JSON-RPC 遥控器；
- **codex sdk** 也是独立子进程（`codex app-server`），形态和 dsh 很像，来自 OpenAI。

### deepagents：backend 就是虚拟盘

第 8 课把一个 `VirtualFS` 交给 `create_deep_agent(..., backend=FS)`。模型的 `write_file` / `read_file` 全是调这个对象的方法，磁盘路径由你映射；`execute` 直接从工具列表里排除，所以没有 shell。技能是 `/skills/` 下的 `SKILL.md`，中间件启动时读进提示词。长期记忆也是虚拟路径上的文件。

要点：文件系统是一等公民，换后端（本地目录、数据库、对象存储）不用改模型侧的工具名。

### dsh：patch 插插件

第 9 课用 `sdk-minimal`，它自带 bash、不带 `ctx.fs`。我们在 patch 里做了四件事：关掉 `persistent-bash` 和会话日志上传；插入自己的 `fs-virtual.mjs`（`ctx.provide('fs', …)`，底下是 `LocalDiskStorage` + 未实现的 `RemoteApiStorage`）；插入官方的 `@deepseek-ai/dsh-tool-fs`、`dsh-skill`、`dsh-skill-filesystem`、`dsh-tool-skill`；再插一个天气插件。

模型侧看到的是 `read` / `write` / `edit`、`skill`、`get_weather`。文件工具不碰盘，只调 `ctx.fs`；技能从 `customSkillDirs` 发现。本地插件 import 不到官方 TS 包，所以 `fs-virtual` 只能「同形状」，`instanceof FsError` 对不上——实测「文件不存在」仍能回到模型侧，但要心里有数。

要点：扩展全是插件 + patch，和第八课的「传参」是两种世界观。

### Codex：技能有，shell 关不掉，虚拟文件系统不支持

第 10 课的技能放在会话目录上层的 `.agents/skills` 里，Codex 自己发现，模型自己决定要不要用。但 Codex 只给模型技能的名字和路径，正文要模型用 shell 去读，所以 shell 不能关，只能用 `workspace_write` 沙箱和 `deny_all` 审批把写操作关在会话目录里。文件就用它自带的 `apply_patch` 和 shell 读写。

Codex SDK 不支持虚拟文件系统：它自己的 `apply_patch` / shell 直接读写本地磁盘，SDK 没有接口可以接管。

要点：沙箱和审批是它的长处，适合在本地应用中嵌入 Codex 的能力。

### 结论总结：

deepagents适合做服务化，定制化程度更高。dsh、codex sdk适合在客户端嵌入对应的agent（在本地运行）。

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）