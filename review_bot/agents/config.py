"""Configuration for individual review agents. Add a new AgentConfig to the
registry (review_bot/agents/registry.py) to add a new specialized reviewer —
no changes needed anywhere else, the LangGraph in graph.py is built dynamically
from whatever's registered and enabled.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AgentConfig:
    name: str                    # short identifier, e.g. "correctness" — also becomes comments.category
    display_name: str            # human-readable, used in comment prefixes and logs
    focus_instructions: str      # agent-specific section injected into the shared system prompt
    model: Optional[str] = None  # override the default generator model for this agent; None = use default
    use_vulnerability_tool: bool = False  # if True, dependency manifest changes get an OSV.dev scan injected
