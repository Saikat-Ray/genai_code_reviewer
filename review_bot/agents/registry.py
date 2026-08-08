"""Registry of available review agents.

To add a new agent:
  1. Define an AgentConfig below (or call register_agent(...) at runtime).
  2. Add its `name` to the AGENT_NAMES env var (comma-separated) if it should
     be active by default, or call enable_agent("your_agent_name").
No other code needs to change — review_bot/agents/graph.py builds one parallel
branch per enabled agent automatically.
"""

import os

from review_bot.agents.config import AgentConfig

CORRECTNESS_AGENT = AgentConfig(
    name="correctness",
    display_name="Correctness Reviewer",
    focus_instructions="""Focus ONLY on logical correctness. Look for:
- Logic errors, incorrect conditionals, off-by-one errors
- Incorrect API usage, wrong function arguments, type mismatches
- Null/undefined/None handling issues
- Swallowed exceptions, missing error checks on fallible operations
- Incorrect retry/timeout/concurrency logic

Do NOT comment on security vulnerabilities, vulnerable dependencies, or style —
other agents cover those.""",
)

SECURITY_AGENT = AgentConfig(
    name="security",
    display_name="Security Reviewer",
    focus_instructions="""Focus ONLY on security. Look for:
- Injection risks (SQL, command, path traversal, template injection, etc.)
- Unsafe deserialization
- Hardcoded secrets, credentials, tokens, or API keys
- Missing or broken authentication/authorization checks
- Unsafe file/path handling
- Insecure randomness, weak/broken crypto, improper certificate or TLS handling

If this file is a dependency manifest, you may be shown a real vulnerability
scan of the changed dependencies below the diff — treat those results as
ground truth, not something to second-guess, and flag any real findings by
pointing your comment at the line that introduced or changed that dependency.

Do NOT comment on general logic bugs or style — other agents cover those.""",
    use_vulnerability_tool=True,
)

# All agents that exist, whether or not they're currently enabled. Keyed by
# name for O(1) lookup when enabling/disabling.
ALL_AGENTS: dict[str, AgentConfig] = {
    agent.name: agent for agent in [CORRECTNESS_AGENT, SECURITY_AGENT]
}


def _load_enabled_agents() -> list[AgentConfig]:
    """AGENT_NAMES env var (comma-separated) controls which registered agents
    actually run. Defaults to all registered agents if unset. Unknown names
    are ignored with a warning rather than raising, so a typo in the env var
    doesn't take down the whole review.
    """
    raw = os.environ.get("AGENT_NAMES", ",".join(ALL_AGENTS.keys()))
    names = [n.strip() for n in raw.split(",") if n.strip()]

    enabled = []
    for name in names:
        if name in ALL_AGENTS:
            enabled.append(ALL_AGENTS[name])
        else:
            print(f"registry: AGENT_NAMES referenced unknown agent '{name}', ignoring")
    return enabled


ENABLED_AGENTS: list[AgentConfig] = _load_enabled_agents()


def register_agent(agent: AgentConfig):
    """Add a new agent at runtime without editing this module (e.g. from a
    future plugin system or config file). Does not auto-enable it.
    """
    ALL_AGENTS[agent.name] = agent


def enable_agent(name: str):
    if name not in ALL_AGENTS:
        raise ValueError(f"Unknown agent '{name}' — register it first via register_agent().")
    if not any(a.name == name for a in ENABLED_AGENTS):
        ENABLED_AGENTS.append(ALL_AGENTS[name])


def describe_registry(default_model: str = "gpt-5") -> str:
    """Human-readable summary of currently enabled agents/models, for logging
    into the `reviews.generator_model` column so you can trace which config
    produced a given run's comments."""
    return ", ".join(
        f"{agent.name}={agent.model or default_model}" for agent in ENABLED_AGENTS
    )
