"""用 langfuse 的 dataset + experiment 给第 8 课的 deepagents agent 打分。

    .venv/bin/python src/lesson11/eval.py
"""
import uuid

from langchain_core.messages import AIMessage
from langfuse import Evaluation
from pydantic import BaseModel, Field

from tracing import Agent, langfuse, model

DATASET = "lesson11-deepagents"

# 题目对齐第 8 课：虚拟文件系统 + 技能
ITEMS = [
    {
        "id": "deepagents-write",
        "input": "在 /workspace/eval-note.txt 写一行：hello-eval，然后告诉我路径",
        "expected": "/workspace/eval-note.txt",
        "want_tools": ["write_file"],
    },
    {
        "id": "deepagents-skill",
        "input": "帮我把这段会议记录整理成纪要：周一上午十点评审，小张负责改稿，周三前交",
        "expected": "按主题、结论、待办、未决问题四段输出，待办里有小张改稿、周三前",
        "want_tools": ["read_file"],
    },
]


def ensure_dataset(lf):
    try:
        lf.get_dataset(DATASET)
    except Exception:
        lf.create_dataset(name=DATASET, description="第 11 课：第 8 课 deepagents（虚拟文件系统 + 技能）回归用例")
    for row in ITEMS:
        lf.create_dataset_item(
            id=row["id"],
            dataset_name=DATASET,
            input=row["input"],
            expected_output=row["expected"],
            metadata={"want_tools": row["want_tools"]},
        )
    return lf.get_dataset(DATASET)


def task(*, item, **kwargs):
    """每条用例一个新会话，直接调图，拿回答和用过的工具名。"""
    agent = Agent(f"eval-{uuid.uuid4()}")
    agent.fs.bind(agent.context_id)
    result = agent.graph.invoke({"messages": [{"role": "user", "content": item.input}]}, agent.config())
    messages = result["messages"]
    tools = [call["name"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]
    return {"answer": str(messages[-1].text), "tools": tools}


def contains(*, output, expected_output, metadata=None, **kwargs):
    """参考答案里的路径是否出现在回答里（只给写文件那题用）。"""
    if not str(expected_output).startswith("/"):
        return Evaluation(name="contains", value=1.0, comment="不适用")
    hit = str(expected_output) in (output.get("answer") or "")
    return Evaluation(name="contains", value=1.0 if hit else 0.0)


def used_tools(*, output, metadata=None, **kwargs):
    """该出现的工具名是否都出现过。"""
    want = list((metadata or {}).get("want_tools") or [])
    got = output.get("tools") or []
    ok = all(w in got for w in want)
    return Evaluation(name="tool_choice", value=1.0 if ok else 0.0, comment=f"want={want} got={got}")


class Verdict(BaseModel):
    score: float = Field(description="0 到 1 之间，1 表示回答正确且切题")
    reason: str = Field(description="一句话理由")


def llm_judge(*, input, output, expected_output, **kwargs):
    reply = model.invoke(
        "你是严格的评分员。根据参考答案判断回答是否正确，只按事实是否一致打分，不看文风。\n"
        f"问题：{input}\n参考答案：{expected_output}\n回答：{output['answer']}\n"
        f"用过的工具：{output.get('tools')}\n"
        '只输出一行 JSON：{"score": 0到1的小数, "reason": "一句话理由"}'
    )
    text = str(reply.text)
    verdict = Verdict.model_validate_json(text[text.index("{"): text.rindex("}") + 1])
    return Evaluation(name="llm_judge", value=max(0.0, min(1.0, verdict.score)), comment=verdict.reason)


def average(*, item_results, **kwargs):
    scores = [e.value for r in item_results for e in r.evaluations if e.name == "llm_judge"]
    return Evaluation(name="avg_llm_judge", value=sum(scores) / len(scores) if scores else 0.0)


def main():
    lf = langfuse()
    print("langfuse auth:", lf.auth_check())
    dataset = ensure_dataset(lf)
    result = dataset.run_experiment(
        name="lesson8-deepagents",
        description="第 8 课 deepagents：虚拟文件系统 + 技能",
        task=task,
        evaluators=[contains, used_tools, llm_judge],
        run_evaluators=[average],
        max_concurrency=1,  # VirtualFS 一次只绑一个会话，串行跑
    )
    print(result.format(include_item_results=True))
    lf.flush()


if __name__ == "__main__":
    main()
