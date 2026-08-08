"""Shared candidate-comment data shape, used by every review agent."""

from dataclasses import dataclass


@dataclass
class CandidateComment:
    line_number: int
    category: str          # set to the producing agent's name (e.g. "correctness", "security")
    comment_text: str
    self_assessment: str   # 'high' | 'medium' | 'low' — agent's own confidence, informational only
    source_agent: str = "" # display name of the agent that produced this, for logs/DB
