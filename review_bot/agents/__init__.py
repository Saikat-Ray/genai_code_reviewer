"""Multi-agent code review via LangGraph. Each registered agent (see
agents/registry.py) is a specialized reviewer scoped to one category of
issue; they run in parallel over the same file diff and their output is
merged and deduped before grading.

Public API used by main.py:
    run_agents(...)       — run all enabled agents, return merged candidates
    describe_registry(...) — human-readable "name=model" summary for logging

To add a new agent: define an AgentConfig in registry.py and add it to
ALL_AGENTS. No changes needed here or in graph.py — the graph is built
dynamically from whatever's registered and enabled.
"""

from review_bot.agents.graph import run_agents
from review_bot.agents.registry import (
    ALL_AGENTS,
    ENABLED_AGENTS,
    describe_registry,
    enable_agent,
    register_agent,
)

__all__ = [
    "run_agents",
    "describe_registry",
    "enable_agent",
    "register_agent",
    "ALL_AGENTS",
    "ENABLED_AGENTS",
]
