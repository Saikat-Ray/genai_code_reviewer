"""LangChain chat model construction, dispatched by model name prefix — mirrors
review_bot.llm_client's dispatch logic (used by the single-model grader), but
returns LangChain BaseChatModel instances for use inside LangGraph agent nodes.
"""

import os

DEFAULT_MAX_TOKENS = int(os.environ.get("GENERATOR_MAX_TOKENS", "4096"))


def get_chat_model(model: str, max_tokens: int | None = None):
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS

    if model.startswith("claude"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, max_tokens=max_tokens)

    elif model.startswith("gpt") or model.startswith("o"):
        from langchain_openai import ChatOpenAI
        # GPT-5/o-series reasoning models consume max_completion_tokens for
        # internal reasoning before producing visible output — same caveat as
        # review_bot.llm_client._call_openai. Passed via model_kwargs rather
        # than ChatOpenAI's own max_tokens param, so it reaches the API call
        # exactly the same way as the raw SDK path does, for consistency.
        return ChatOpenAI(model=model, model_kwargs={"max_completion_tokens": max_tokens})

    else:
        raise ValueError(f"get_chat_model: unrecognized model prefix for '{model}'")
