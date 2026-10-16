import logging
from typing import TypedDict

import requests
from bs4 import BeautifulSoup
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 工作流内部的模型调用不进外层 agent 的 token 流
QUIET = {"tags": ["nostream"]}
MAX_STEPS = 3


class Research(TypedDict, total=False):
    question: str
    plan: str
    steps: list[str]
    index: int
    hits: str
    notes: list[str]
    reply: str


def _text(message) -> str:
    text = str(message.text).strip()
    if text:
        return text
    extra = getattr(message, "additional_kwargs", None) or {}
    return str(extra.get("reasoning_content") or "").strip()


class SearchPlan(BaseModel):
    steps: list[str] = Field(description="按顺序执行的搜索词，后一次可以依赖前一次。最多 3 条。")


class StepFacts(BaseModel):
    facts: list[str] = Field(description="这一步搜索结果里有的事实，每条一句。没有就空列表。")


def render_plan(steps: list[str]) -> str:
    # 执行计划就是这句 prompt：先搜索 xxx，后搜索 xxx
    lines = [f"先搜索 {steps[0]}"]
    lines.extend(f"后搜索 {step}" for step in steps[1:])
    return "\n".join(lines)


def web_search(query: str, limit: int = 4) -> str:
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.info("search failed %s", exc)
        return "没有搜索结果"
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for link, snippet in zip(soup.select("a.result__a"), soup.select(".result__snippet")):
        title = link.get_text(" ", strip=True)
        text = snippet.get_text(" ", strip=True)
        if title:
            rows.append(f"- {title}\n  {text}")
        if len(rows) >= limit:
            break
    return "\n".join(rows) if rows else "没有搜索结果"


def build_web_search(model: BaseChatModel, search_fn=web_search):
    # 思考模式不接受指定的 tool_choice，结构化输出走 function_calling
    model = model.model_copy(update={"extra_body": {"thinking": {"type": "disabled"}}})
    plan_model = model.with_structured_output(SearchPlan, method="function_calling")
    facts_model = model.with_structured_output(StepFacts, method="function_calling")

    def plan(state: Research) -> dict:
        question = state.get("question", "").strip()
        if not question:
            return {"steps": [], "reply": "没有问题。"}
        parsed = plan_model.invoke(
            [
                {"role": "system", "content": "把问题拆成联网搜索。不要回答问题。"},
                {"role": "user", "content": question},
            ],
            config=QUIET,
        )
        steps = parsed.steps[:MAX_STEPS] or [question]
        plan_text = render_plan(steps)
        logger.info("plan %s", plan_text.replace("\n", " | "))
        return {"steps": steps, "plan": plan_text, "index": 0, "notes": []}

    def route_after_plan(state: Research) -> str:
        if state.get("steps"):
            return "search"
        return END

    def search(state: Research) -> dict:
        query = state["steps"][state["index"]]
        logger.info("search %s", query)
        return {"hits": search_fn(query)}

    def execute(state: Research) -> dict:
        query = state["steps"][state["index"]]
        parsed = facts_model.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "按计划执行当前这一步。只根据这一步的搜索结果填写，不要回答整个问题。\n\n"
                        f"{state['plan']}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"问题：{state['question']}\n当前：搜索 {query}\n结果：\n{state.get('hits', '')}",
                },
            ],
            config=QUIET,
        )
        text = "\n".join(parsed.facts) or "未查到"
        notes = [*state.get("notes", []), f"搜索 {query}\n{text}"]
        logger.info("execute %s/%s", state["index"] + 1, len(state["steps"]))
        return {"notes": notes, "index": state["index"] + 1}

    def route_after_execute(state: Research) -> str:
        if state["index"] < len(state["steps"]):
            return "search"
        return "summary"

    def summary(state: Research) -> dict:
        found = "\n\n".join(state.get("notes", []))
        message = model.invoke(
            [
                {
                    "role": "system",
                    "content": "计划已经执行完。只根据各步事实回答问题，没查到的就说明没查到。",
                },
                {
                    "role": "user",
                    "content": (
                        f"问题：{state['question']}\n"
                        f"计划：\n{state.get('plan', '')}\n\n"
                        f"{found}"
                    ),
                },
            ],
            config=QUIET,
        )
        return {"reply": f"{state.get('plan', '')}\n\n{_text(message)}"}

    graph = StateGraph(Research)
    graph.add_node("plan", plan)
    graph.add_node("search", search)
    graph.add_node("execute", execute)
    graph.add_node("summary", summary)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_after_plan)
    graph.add_edge("search", "execute")
    graph.add_conditional_edges("execute", route_after_execute)
    graph.add_edge("summary", END)
    return graph.compile()


def build_workflows(model: BaseChatModel) -> dict:
    return {
        "web-search": {
            "description": "需要联网才能回答的问题。先写成「先搜索…，后搜索…」的计划，再逐步搜索、执行，最后总结。闲聊不要调用。",
            "graph": build_web_search(model),
        },
    }
