---
name: memo
description: 按固定格式把一条备忘写进当前目录的 memos/ 文件夹。
whenToUse: 用户说记一条备忘、帮我记下来、存个 memo 之类的话时使用。
---

# 备忘格式

1. 文件放在 `memos/` 下，文件名用英文小写加连字符，比如 `memos/buy-milk.md`。
2. 用 `write` 工具写文件，内容严格按下面三段：

```
# MEMO: <一句话标题>

<用户要记的内容，原话保留>

-- saved by memo skill
```

3. 写完后用 `read` 工具读一遍确认，再告诉用户文件路径。
