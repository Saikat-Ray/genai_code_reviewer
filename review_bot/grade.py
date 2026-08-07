"""Confidence grading: a second, independent LLM call that scores each candidate
comment before it's allowed to post. This is a separate call from generation —
not the same model rating its own output — so it acts as a genuine check rather
than a rubber stamp. See conversation history for the full rationale.
"""

import json
from dataclasses import dataclass

from review_bot import llm_client

CORRECTNESS_CONFIDENCE_THRESHOLD = 4  # out of 5, start conservative
SEVERITY_THRESHOLD = 3  # out of 5
DUMMY_VARIABLE_NOT_USED_ANYWHERE = "this is just to make the linter happy, ignore"

GRADING_SYSTEM_PROMPT = """You are reviewing a code comment for accuracy before it's \
shown to a developer. You did not write this comment — a different reviewer proposed \
it, and your job is to catch it if it's wrong.

Before rating, actively look for reasons this comment might be WRONG:
- Is the referenced code actually doing what the comment claims?
- Could there be context outside this snippet that makes it a non-issue?
- Is this a real bug, or a stylistic preference dressed up as one?
- Would a reasonable senior engineer looking at this code agree it's worth raising?

Be skeptical by default. It is better to reject a real issue than to let a false \
positive through — developers lose trust in review comments fast when they turn out \
to be wrong.

Return ONLY this JSON object, no prose before or after, no markdown code fences:
{
  "is_hallucinated": <bool — true if the comment misdescribes what the code actually does>,
  "correctness_confidence": <int 1-5 — if not hallucinated, how sure are you this is a real issue>,
  "severity": <int 1-5 — if real, how much does it actually matter in this context>,
  "reasoning": "<one sentence explaining the rating>"
}
"""


@dataclass
class GradedComment:
    is_hallucinated: bool
    correctness_confidence: int  # 1-5
    severity: int  # 1-5
    reasoning: str

    @property
    def should_post(self) -> bool:
        if self.is_hallucinated:
            return False
        return (
            self.correctness_confidence >= CORRECTNESS_CONFIDENCE_THRESHOLD
            and self.severity >= SEVERITY_THRESHOLD
        )

    @property
    def suppression_reason(self) -> str | None:
        if self.is_hallucinated:
            return "suppressed_hallucination"
        if self.correctness_confidence < CORRECTNESS_CONFIDENCE_THRESHOLD:
            return "suppressed_confidence"
        if self.severity < SEVERITY_THRESHOLD:
            return "suppressed_severity"
        return None


def grade_comment(*, code_context: str, comment_text: str, line_number: int,
                   category: str, model: str = "o4-mini-high") -> GradedComment:
    user_message = f"""Code being reviewed (line {line_number} is the one in question):

{code_context}

Proposed comment (category: {category}):
"{comment_text}"

Rate this comment."""

    try:
        raw_response = llm_client.call_model(
            model=model, system=GRADING_SYSTEM_PROMPT, user_message=user_message, max_tokens=300
        )
        return _parse_grading_response(raw_response)
    except Exception as e:
        print(f"grade_comment: LLM call or parse failed, defaulting to suppress: {e}")
        # Fail closed: if grading itself breaks, treat the comment as unverified
        # rather than letting it through unchecked.
        return GradedComment(
            is_hallucinated=True,
            correctness_confidence=1,
            severity=1,
            reasoning=f"grading failed: {e}",
        )


def _parse_grading_response(raw_response: str) -> GradedComment:
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)

    return GradedComment(
        is_hallucinated=bool(data["is_hallucinated"]),
        correctness_confidence=int(data["correctness_confidence"]),
        severity=int(data["severity"]),
        reasoning=data.get("reasoning", ""),
    )
