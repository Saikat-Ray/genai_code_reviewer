"""LangGraph orchestration for running multiple specialized review agents in
parallel over the same file diff/context, then merging their candidate
comments into a single list.

To add a new agent: register it in review_bot/agents/registry.py. This module
builds one parallel branch per enabled agent automatically — nothing here
needs to change.
"""

import json
import operator
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage

from review_bot.agents.config import AgentConfig
from review_bot.agents import registry
from review_bot.agents.prompts import build_system_prompt
from review_bot.agents.llm import get_chat_model
from review_bot.agents.tools import is_dependency_file, scan_dependency_changes
from review_bot.context_formatting import build_user_message
from review_bot.candidate import CandidateComment


class ReviewState(TypedDict):
    file_path: str
    language: str
    diff_hunk: str
    context: dict
    valid_lines: set
    model: str
    # Annotated with operator.add so parallel agent branches can each append
    # their own candidates without one branch's write clobbering another's —
    # this is what makes the fan-out/fan-in pattern below safe.
    candidates: Annotated[list, operator.add]


def _make_agent_node(agent: AgentConfig):
    """Returns a LangGraph node function bound to a specific agent's config."""

    def node(state: ReviewState) -> dict:
        system_prompt = build_system_prompt(agent.focus_instructions)

        extra_context = ""
        if agent.use_vulnerability_tool and is_dependency_file(state["file_path"]):
            extra_context = scan_dependency_changes(state["file_path"], state["diff_hunk"])

        user_message = build_user_message(
            file_path=state["file_path"], language=state["language"],
            diff_hunk=state["diff_hunk"], context=state["context"],
            valid_lines=state["valid_lines"], extra_context=extra_context,
        )
        model_name = agent.model or state["model"]

        try:
            llm = get_chat_model(model_name)
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ])
            raw_text = response.content
        except Exception as e:
            print(f"agents.graph: [{agent.name}] LLM call failed for {state['file_path']} "
                  f"(model={model_name}): {e}")
            return {"candidates": []}

        candidates = _parse_agent_response(raw_text, agent=agent, file_path=state["file_path"])
        return {"candidates": candidates}

    return node


def _parse_agent_response(raw_text: str, *, agent: AgentConfig, file_path: str) -> list[CandidateComment]:
    if not raw_text or not raw_text.strip():
        print(f"agents.graph: [{agent.name}] empty response for {file_path} — "
              f"possibly the token budget was consumed by reasoning; see GENERATOR_MAX_TOKENS.")
        return []

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"agents.graph: [{agent.name}] malformed JSON for {file_path}: {e}\nRaw: {text[:500]}")
        return []

    if not isinstance(items, list):
        print(f"agents.graph: [{agent.name}] expected JSON array for {file_path}, got {type(items)}")
        return []

    candidates = []
    for item in items:
        try:
            candidates.append(CandidateComment(
                line_number=int(item["line_number"]),
                category=agent.name,           # category is fixed per-agent, not model-chosen
                comment_text=item["comment"],
                self_assessment=item.get("confidence_self_assessment", "medium"),
                source_agent=agent.display_name,
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f"agents.graph: [{agent.name}] skipping malformed candidate for {file_path}: {e} — {item}")
            continue

    return candidates


def _build_graph(agents: list[AgentConfig]):
    graph = StateGraph(ReviewState)

    for agent in agents:
        graph.add_node(agent.name, _make_agent_node(agent))
        graph.add_edge(START, agent.name)  # fan-out: every agent runs in parallel from START
        graph.add_edge(agent.name, END)    # fan-in: each agent's output merges via the reducer above

    return graph.compile()


# Compiled once at import time from whatever's currently enabled in the registry.
_compiled_graph = _build_graph(registry.ENABLED_AGENTS)


def rebuild_graph():
    """Call this if you change registry.ENABLED_AGENTS at runtime (e.g. via
    registry.enable_agent(...)) after this module has already been imported —
    the compiled graph is otherwise frozen at import time.
    """
    global _compiled_graph
    _compiled_graph = _build_graph(registry.ENABLED_AGENTS)


def _dedupe_by_line(candidates: list[CandidateComment]) -> list[CandidateComment]:
    """Two agents can independently flag the same line. Only one comment per
    line can actually post — github_client.post_review maps GitHub's response
    back to internal ids by (file_path, line_number), so a collision there
    would silently lose one of the two. Keep the first (registry order) and
    drop the rest, logging what was dropped so it's visible, not silent.
    """
    by_line: dict[int, CandidateComment] = {}
    dropped = 0
    for c in candidates:
        if c.line_number in by_line:
            dropped += 1
            continue
        by_line[c.line_number] = c
    if dropped:
        print(f"agents.graph: dropped {dropped} duplicate candidate(s) — multiple agents flagged the same line")
    return list(by_line.values())


def run_agents(*, file_path: str, language: str, diff_hunk: str, context: dict,
               valid_lines: set[int] | None = None, model: str = "gpt-5") -> list[CandidateComment]:
    """Runs every enabled agent in parallel over the same file diff/context and
    returns the merged, deduped list of candidate comments. Drop-in replacement
    for the old single-prompt generate.generate_comments() call in main.py.
    """
    if not diff_hunk.strip():
        return []

    if not registry.ENABLED_AGENTS:
        print("agents.graph: no agents enabled (check AGENT_NAMES) — returning no candidates")
        return []

    initial_state: ReviewState = {
        "file_path": file_path, "language": language, "diff_hunk": diff_hunk,
        "context": context, "valid_lines": valid_lines or set(), "model": model,
        "candidates": [],
    }
    result = _compiled_graph.invoke(initial_state)
    return _dedupe_by_line(result["candidates"])
