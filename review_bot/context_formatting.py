"""Shared prompt formatting: turns enclosing-function context + a diff hunk into
the user message sent to a review agent. Used by every agent so context
formatting stays identical as you add more agents — only the system prompt
(focus area) differs between them.
"""


def build_user_message(*, file_path: str, language: str, diff_hunk: str,
                        context: dict, valid_lines: set[int] | None = None,
                        extra_context: str = "") -> str:
    """context: dict of {line_number: CodeContext}. Deduplicated so each unique
    enclosing-function block is shown once, even though multiple changed lines
    may point at the same CodeContext object.

    extra_context: optional agent-specific text appended after the diff (e.g.
    a dependency vulnerability scan result for the security agent).
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

    valid_lines_note = ""
    if valid_lines:
        sorted_lines = ", ".join(str(n) for n in sorted(valid_lines))
        valid_lines_note = (
            f"\n\nYou may only set \"line_number\" to one of these exact lines "
            f"(the lines actually changed in this diff): {sorted_lines}\n"
            f"Any comment on a line outside this set will be discarded, so don't propose one."
        )

    message = f"""File: {file_path}
Language: {language}

{context_section}

--- Diff ---
{diff_hunk}
{valid_lines_note}
"""
    if extra_context:
        message += f"\n{extra_context}\n"
    return message
