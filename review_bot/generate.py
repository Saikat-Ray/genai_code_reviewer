"""Comment generation: takes a file's diff + enclosing-function context, calls the
LLM with the generation prompt, and returns structured candidate comments
(pre-grading — the grader in grade.py is a separate, independent pass).
"""

import json
from dataclasses import dataclass

from review_bot import llm_client


@dataclass
class CandidateComment:
    line_number: int
    category: str  # 'bug' | 'security' | 'error_handling' | 'style'
    comment_text: str
    self_assessment: str  # 'high' | 'medium' | 'low' — generator's own confidence, informational only


GENERATION_SYSTEM_PROMPT = """You are an expert code reviewer looking at a single file's \
changes within a pull request. Your job is to find real, specific issues — not to \
comment on everything you notice.

You will be shown one or more enclosing-function context blocks (the full function \
containing each changed region, plus signatures of any same-file helper functions it \
calls) followed by the diff for this file.

Only comment on issues in the ADDED or CHANGED lines (marked with a leading "+" in the \
diff). Do not comment on pre-existing code unless the change directly interacts with a \
bug in it.

Categories to look for, in priority order:
- "bug": logic errors, incorrect conditionals, off-by-one errors, incorrect API usage,
  null/undefined handling issues
- "security": injection risks, unsafe deserialization, hardcoded secrets, missing auth
  checks, unsafe file/path handling
- "error_handling": swallowed exceptions, missing error checks on fallible operations,
  incorrect retry/timeout logic
- "style": naming, formatting, minor conventions — ONLY flag if it violates an explicit
  convention visible in the surrounding code (e.g. every other function in this file
  does X, this one doesn't)

Do NOT comment on:
- Anything already covered by a linter (indentation, unused imports, line length)
- Speculative "could be better" suggestions with no concrete failure mode
- Missing tests, missing docs — unless explicitly asked
- Anything you're not reasonably confident is actually in the code shown to you

If you find nothing worth flagging, return an empty list. An empty list is a GOOD
outcome — do not manufacture comments to have something to say.

For each issue, output:
{
  "line_number": <int, the exact line in the NEW file, matching the diff's line numbers>,
  "category": "bug" | "security" | "error_handling" | "style",
  "comment": "<specific, actionable, 1-3 sentences. Reference the actual variable/function names.>",
  "confidence_self_assessment": "high" | "medium" | "low"
}

Return ONLY a JSON array. No prose before or after, no markdown code fences.
"""


def _build_user_message(*, file_path: str, language: str, diff_hunk: str,
                         context: dict) -> str:
    """context: dict of {line_number: CodeContext}, as produced by main.py.
    Deduplicated so each unique enclosing-function block is shown once, even
    though multiple changed lines may point at the same CodeContext object.
    """
    seen_ids = set()
    context_blocks = []

    for line_number in sorted(context.keys()):
        ctx = context[line_number]
        if id(ctx) in seen_ids:
            continue
        seen_ids.add(id(ctx))

        block = f"--- Context for lines {ctx.start_line}-{ctx.end_line} ---\n"
        if ctx.related_signatures:
            block += "Same-file functions called here (signatures only):\n"
            block += "\n".join(f"  {sig}" for sig in ctx.related_signatures) + "\n\n"
        block += ctx.enclosing_function_text
        context_blocks.append(block)

    context_section = "\n\n".join(context_blocks) if context_blocks else "(no context extracted)"

    return f"""File: {file_path}
Language: {language}

{context_section}

--- Diff ---
{diff_hunk}
"""


def generate_comments(*, file_path: str, language: str, diff_hunk: str,
                       context: dict, model: str = "claude-sonnet-4-5") -> list[CandidateComment]:
    if not diff_hunk.strip():
        return []

    user_message = _build_user_message(
        file_path=file_path, language=language, diff_hunk=diff_hunk, context=context
    )

    try:
        raw_response = llm_client.call_model(model=model, system=GENERATION_SYSTEM_PROMPT, user_message=user_message)
    except Exception as e:
        print(f"generate_comments: LLM call failed for {file_path}: {e}")
        return []

    return _parse_response(raw_response, file_path=file_path)


def _parse_response(raw_response: str, *, file_path: str) -> list[CandidateComment]:
    text = raw_response.strip()

    # Models sometimes wrap JSON in markdown fences despite instructions not to.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"generate_comments: malformed JSON from model for {file_path}: {e}\nRaw: {text[:500]}")
        return []

    if not isinstance(items, list):
        print(f"generate_comments: expected JSON array for {file_path}, got {type(items)}")
        return []

    candidates = []
    for item in items:
        try:
            candidates.append(CandidateComment(
                line_number=int(item["line_number"]),
                category=item["category"],
                comment_text=item["comment"],
                self_assessment=item.get("confidence_self_assessment", "medium"),
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f"generate_comments: skipping malformed candidate for {file_path}: {e} — {item}")
            continue

    return candidates
