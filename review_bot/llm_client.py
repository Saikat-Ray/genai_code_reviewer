"""Shared provider-agnostic LLM call dispatch, used by grade.py (the
independent grading pass) and, if still called anywhere, the legacy
single-agent generate.py. Model name prefix decides which SDK handles the
call, so callers just pass a model string without needing to know which
provider it maps to.

The multi-agent generation path (review_bot/agents/) uses its own LangChain
chat model factory — see agents/llm.py — since it needs LangChain's
invoke()-over-messages interface rather than raw SDK calls.
"""

import os


def call_model(*, model: str, system: str, user_message: str, max_tokens: int = 2048) -> str:
    if model.startswith("claude"):
        return _call_claude(model=model, system=system, user_message=user_message, max_tokens=max_tokens)
    elif model.startswith("gpt") or model.startswith("o"):
        return _call_openai(model=model, system=system, user_message=user_message, max_tokens=max_tokens)
    else:
        raise ValueError(f"call_model: unrecognized model prefix for '{model}'")


def _call_claude(*, model: str, system: str, user_message: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _call_openai(*, model: str, system: str, user_message: str, max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI()  # reads OPENAI_API_KEY from env
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,  # OpenAI deprecated max_tokens; this is current guidance
        messages=[
            {"role": "system", "content": system},  # auto-converted to 'developer' role for reasoning models
            {"role": "user", "content": user_message},
        ],
    )

    choice = response.choices[0]
    content = choice.message.content

    if not content:
        # GPT-5 family and o-series models consume reasoning tokens against the
        # SAME max_completion_tokens budget as the visible output. If the prompt
        # is large (e.g. a full function's context + diff) or the model reasons
        # a lot, it can burn the entire budget on internal reasoning and leave
        # zero tokens for the actual answer — producing an empty string here.
        # finish_reason='length' confirms truncation rather than some other cause.
        usage = getattr(response, "usage", None)
        reasoning_tokens = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None)
        raise RuntimeError(
            f"OpenAI returned empty content for model '{model}'. "
            f"finish_reason={choice.finish_reason!r}, "
            f"reasoning_tokens_used={reasoning_tokens}, max_completion_tokens={max_tokens}. "
            f"Likely cause: the token budget was entirely consumed by internal reasoning "
            f"before any visible output could be produced. Increase max_tokens (via "
            f"GENERATOR_MAX_TOKENS / GRADER_MAX_TOKENS env vars) and retry."
        )

    return content
