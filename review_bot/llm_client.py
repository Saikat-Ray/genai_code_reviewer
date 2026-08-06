"""Shared provider-agnostic LLM call dispatch, used by both generate.py and
grade.py. Model name prefix decides which SDK handles the call, so callers
just pass a model string without needing to know which provider it maps to.
"""


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
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content
