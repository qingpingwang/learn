### skills的加载流程

skills的采用的是**渐进式披露**的思想，还是上下文限制的原因，我们需要尽可能节省宝贵的上下文Token。这里的渐进式披露，指的是分成两层：**名称和描述**每轮都放进上下文，**正文**只在需要触发这个 skill 的时候才加载（一般不主动卸载）。
首先，skills的管理是在固定目录下，在agent首轮对话的时候，会将skills目录里的全部skill的名称、描述全部加进上下文里。
全部的skill基本信息必须**每轮都在**，否则模型不知道有哪些 skill 可用。但它很便宜：几十个 skill 也就几百 token。

### skills的格式描述

一个 skill 就是一个目录，目录名和 skill 的名字保持一致。必须有一个 `SKILL.md`，附属文件可有可无：

```text
skills/send-email/
├── SKILL.md                 # 必须有
└── references/template.md   # 模板、细则，按需再读
```

`SKILL.md` 分成两段，用 `---` 隔开。上半段只有两行，下半段是真正的操作步骤：

```markdown
---
name: send-email
description: 起草并确认邮件。用户要发邮件、写邮件、回复邮件时使用。
---

# 发邮件

按这个顺序做，不要跳步：

1. 确认收件人、主题、目的。缺了就问，不要编。
2. 调用 read_skill_file，name 为 send-email，path 为 references/template.md，按里面的格式起草正文。
3. 把草稿给用户确认。用户没明确同意之前，不要说已经发出。
4. 用户同意后，给出最终邮件。这里只生成草稿，不真正发送。
```

这两段进上下文的时机不一样：

- `name` 和 `description` **每轮都在**。模型靠 `description` 判断要不要用这个 skill，所以这句话要写清楚**什么时候用**，而不是写 skill 是干什么的。`send-email` 写的是“用户要发邮件、写邮件、回复邮件时使用”，而不是“一个发邮件的工具”。
- `---` 下面的正文只有调用 `load_skill` **之后才进来**。这里写步骤、顺序、禁止事项。步骤要短，能让模型照着做，不要写成说明文。
- `references/` 里的文件**不会自动出现**。哪一步要用，就在步骤里写明调用 `read_skill_file`，并给出 `name` 和 `path`。`meeting-notes` 没有附属文件，所以它的目录里只有一个 `SKILL.md`。

我们的解析很简单：读到第二个 `---` 为止，按行切 `key: value`。所以 `name` 和 `description` 都写在一行里，不要换行。

```python
# 获取到目录下所有skills的描述、body、dir
def load_skills():
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        front, _, body = text.removeprefix("---").partition("---")
        meta = {}
        for line in front.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        skills[meta["name"]] = {"description": meta["description"], "body": body.strip(), "dir": path.parent}
    return skills
```

### 上下文的组织

我们这次简单点，在**动态提示词**中完成这一部分拼接。

1. **全部的 skills**：只有名称和描述；
2. **已加载的 skills**：`load_skill` 之后的正文；

```python
def dynamic_system_prompt(agent, system_prompt: str) -> str:
    catalog = "\n".join(f"- {name}: {skill['description']}" for name, skill in SKILLS.items())
    loaded = "".join(f"\n\n## 已加载 skill：{name}\n{body}" for name, body in agent.loaded_skills.items())
    return (
        f"{system_prompt}当前的系统时间是：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。\n\n"
        f"可用 skills，匹配到用户任务时先调用 load_skill，不要凭记忆执行：\n{catalog}{loaded}"
    )
```



### 必要的工具

这里要给 agent 提供两个工具，让它自己去加载 skill。

1. `load_skill`：加载对应 skill 的正文；
2. `read_skill_file`：读取 skill 目录里的附件。

`read_skill_file` 的路径要**限制在这个 skill 自己的目录里**，避免读到目录外面的文件。

```python
# 加载skill
def load_skill(agent, args):
    name = args.get("name", "")
    skill = SKILLS.get(name)
    if skill is None:
        return f"没有这个 skill，可用的有：{', '.join(SKILLS)}"
    agent.loaded_skills[name] = skill["body"]
    return f"已加载 {name}，按系统提示词里的步骤执行"

# 读取skill文件/附件
def read_skill_file(args):
    skill = SKILLS.get(args.get("name", ""))
    if skill is None:
        return "没有这个 skill"
    root = skill["dir"].resolve()
    target = (root / args.get("path", "")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return "文件不存在"
    return target.read_text(encoding="utf-8")
```

到这里，把这两个工具加进 tools 里，agent 就能根据需求加载对应的 skill，以及读取对应的附件了。

### 调用结果

这里以生成邮件为例，看看agent的渐进式加载skills的过程：

```text
> 你好 
你好！我是测试助手，可以帮你：

- **整理会议纪要**：把零散的会议记录整理成规范纪要和待办
- **起草邮件**：帮你写邮件或回复邮件

有需要直接说，或者把内容发给我就行。
> 你能做什么？
我能帮你做两类事：

1. **整理会议纪要** —— 你把会议记录（哪怕是零散的口语化笔记）发给我，我整理成规范纪要，包含结论和待办事项。
2. **起草邮件** —— 告诉我收件人、目的和要点，我帮你写邮件；也可以帮你拟回复。

直接把内容发过来就行。
> 帮我写一封邮件
call_00_mUl2IgjH0t2At7XBhe9w4723 load_skill   {  "  name  "  :   "  send  -  email  "  }
result: call_00_mUl2IgjH0t2At7XBhe9w4723 load_skill {"name": "send-email"} 已加载 send-email，按系统提示词里的步骤执行
好的，起草邮件前我需要先确认三件事：

1. **收件人**：发给谁？（个人/某个部门/客户）
2. **主题**：这封邮件是关于什么的？
3. **目的**：你希望对方读完做什么？（比如确认信息、安排会议、催进度、致谢……）

把这几点告诉我，如果有具体内容要点也一并给我，我就开始起草。
> 发给小王， 主题是学习，目的是让他列学习计划
call_00_yXGIigjwCqu1Etz35lrS1852 read_skill_file   {  "  name  "  :   "  send  -  email  "  ,   "  path  "  :   "  references  /t  emplate  .md  "  }
result: call_00_yXGIigjwCqu1Etz35lrS1852 read_skill_file {"name": "send-email", "path": "references/template.md"} # 邮件模板

收件人：
主题：

正文：
开头用一句说明来意。
中间写要对方做什么，或需要对方知道的事实。
结尾写期望的回复时间。

落款用“测试助手”。

草稿如下，有两处需要你补一下（用【】标出）：

---

**收件人**：小王
**主题**：学习

**正文**：

小王，你好：

关于学习安排，需要你这边先出一份学习计划。

请围绕【学习内容/方向】列出计划，包含学习目标、具体内容和时间节点，于【期望回复时间】前发我。

如有不清楚的地方，随时沟通。

测试助手

---

请补充：
1. **学习内容/方向**（比如某项技术、业务知识、证书考试？）
2. **希望他什么时候前交计划**
3. 是否需要抄送其他人

补上后我给你出最终版本。
```

可以看到：skill 的正文**并不是一开始就加载的**。我们说“帮我写一封邮件”之后，才调用 `load_skill`。说清收件人、主题、目的之后，才去读附件。

下一章节中，我们将完成多agent的开发，以及相互的协作方式。

仓库地址：[https://github.com/qingpingwang/learn](https://github.com/qingpingwang/learn)

交流群（QQ）：[图形/渲染/音视频/AI应用交流群](https://qm.qq.com/q/2d9YzGPNmQ)（523219063）
