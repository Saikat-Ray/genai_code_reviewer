"""Entry point for the /review command workflow (.github/workflows/review.yml).

Pipeline: fetch PR + diff -> filter/rank/cap files -> extract context ->
generate comments -> grade comments -> post accepted comments -> log everything.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from review_bot import db, github_client, filtering, context, agents, grade

# Generation is now multi-agent (see review_bot/agents/ — correctness + security
# agents running in parallel via LangGraph, configurable via AGENT_NAMES env var
# and the registry in agents/registry.py). Grading stays a single independent
# pass. o4-mini-high (the earlier grader default) has been retired by OpenAI as
# of Feb 2026 — see https://openai.com/index/retiring-gpt-4o-and-older-models/
# — current guidance is to use GPT-5 family model strings.
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

# Fallback model for any agent that doesn't set its own `model` override in
# its AgentConfig (see agents/config.py).
GENERATOR_MODEL = os.environ.get("GENERATOR_MODEL", "gpt-5")
GRADER_MODEL = os.environ.get("GRADER_MODEL", "gpt-5-mini")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    print("review_bot.main: starting")

    repo_name = _require_env("REPO")
    pr_number = int(_require_env("PR_NUMBER"))
    head_sha = _require_env("HEAD_SHA")
    triggered_by = _require_env("TRIGGERED_BY")

    conn = db.get_connection()
    repo = github_client.get_repo()
    pr = github_client.get_pr(repo, pr_number)

    review_id = db.insert_review(
        conn,
        repo=repo_name,
        pr_number=pr_number,
        commit_sha=head_sha,
        triggered_by=triggered_by,
        generator_model=agents.describe_registry(default_model=GENERATOR_MODEL),
        grader_model=GRADER_MODEL,
    )
    print(f"review_bot.main: created review_id={review_id}")

    all_files = github_client.get_changed_files(pr)
    files_to_review, skips = filtering.filter_and_cap_files(all_files)

    for file_path, reason in skips:
        db.insert_file_skip(conn, review_id=review_id, file_path=file_path, reason=reason)

    print(f"review_bot.main: {len(files_to_review)} files to review, "
          f"{len(skips)} skipped")

    # Comments pending post, tagged with their (not-yet-assigned) DB row id so we
    # can map GitHub's response back after posting.
    pending_post: list[dict] = []
    # (comment_id, file_path, line_number, category, comment_text,
    #  is_hallucinated, correctness_confidence, severity, grader_reasoning) for
    # comments already logged as suppressed — nothing more to do with these.
    suppressed_count = 0

    for file_diff in files_to_review:
        language = _infer_language(file_diff.filename)
        diff_lines = github_client.get_diff_lines(file_diff)

        try:
            file_content = repo.get_contents(file_diff.filename, ref=head_sha).decoded_content.decode()
        except Exception as e:
            print(f"main: could not fetch content for {file_diff.filename}: {e}")
            continue

        # Extract enclosing-function context per changed line, but skip lines
        # that fall inside a range we've already extracted — a function with
        # 20 changed lines shouldn't trigger 20 separate tree-sitter walks.
        changed_line_contexts = {}
        extracted_ranges = []  # list of (start_line, end_line, CodeContext)

        for line in sorted(diff_lines):
            existing = next(
                (c for (s, e, c) in extracted_ranges if s <= line <= e), None
            )
            if existing is not None:
                changed_line_contexts[line] = existing
                continue

            ctx = context.get_enclosing_function(file_content, language, line)
            extracted_ranges.append((ctx.start_line, ctx.end_line, ctx))
            changed_line_contexts[line] = ctx

        candidates = agents.run_agents(
            file_path=file_diff.filename,
            language=language,
            diff_hunk=file_diff.patch or "",
            context=changed_line_contexts,
            valid_lines=diff_lines,
            model=GENERATOR_MODEL,
        )
        print(f"main: {file_diff.filename} — {len(candidates)} candidate(s) after multi-agent generation + dedup")

        for candidate in candidates:
            if candidate.line_number not in diff_lines:
                # Can't post here even if it grades well — GitHub will reject it.
                # Log as suppressed rather than silently dropping.
                db.insert_comment(
                    conn, review_id=review_id, file_path=file_diff.filename,
                    line_number=candidate.line_number, category=candidate.category,
                    comment_text=candidate.comment_text, is_hallucinated=None,
                    correctness_confidence=None, severity=None,
                    grader_reasoning="line not in diff, skipped grading",
                    status="suppressed_invalid_line",
                )
                suppressed_count += 1
                continue

            # TODO: pass real enclosing-function context once context.py is implemented
            graded = grade.grade_comment(
                code_context=file_content,
                comment_text=candidate.comment_text,
                line_number=candidate.line_number,
                category=candidate.category,
                model=GRADER_MODEL,
            )

            if graded.should_post:
                comment_id = db.insert_comment(
                    conn, review_id=review_id, file_path=file_diff.filename,
                    line_number=candidate.line_number, category=candidate.category,
                    comment_text=candidate.comment_text,
                    is_hallucinated=graded.is_hallucinated,
                    correctness_confidence=graded.correctness_confidence,
                    severity=graded.severity, grader_reasoning=graded.reasoning,
                    status="posted",  # optimistic; corrected below if posting fails
                )
                pending_post.append({
                    "internal_id": comment_id,
                    "file_path": file_diff.filename,
                    "line_number": candidate.line_number,
                    "body": _format_comment_body(candidate),
                })
            else:
                db.insert_comment(
                    conn, review_id=review_id, file_path=file_diff.filename,
                    line_number=candidate.line_number, category=candidate.category,
                    comment_text=candidate.comment_text,
                    is_hallucinated=graded.is_hallucinated,
                    correctness_confidence=graded.correctness_confidence,
                    severity=graded.severity, grader_reasoning=graded.reasoning,
                    status=graded.suppression_reason,
                )
                suppressed_count += 1

    posted_count = 0
    if pending_post:
        posted_ids = github_client.post_review(
            pr, commit_sha=head_sha, accepted_comments=pending_post
        )
        posted_count = len(posted_ids)

        for internal_id, github_comment_id in posted_ids.items():
            db.update_comment_github_id(conn, comment_id=internal_id, github_comment_id=github_comment_id)

        # Anything we optimistically marked 'posted' but that GitHub didn't
        # actually accept (bad line, etc.) needs correcting.
        failed_ids = [c["internal_id"] for c in pending_post if c["internal_id"] not in posted_ids]
        for failed_id in failed_ids:
            db.update_comment_status(conn, comment_id=failed_id, status="post_failed")

    github_client.post_summary_comment(
        pr,
        body=_build_summary(
            files_reviewed=len(files_to_review), files_skipped=len(skips),
            posted_count=posted_count, suppressed_count=suppressed_count,
        ),
    )

    db.update_review_counts(
        conn, review_id=review_id,
        files_reviewed=len(files_to_review), files_skipped=len(skips),
    )

    print(f"review_bot.main: done. posted={posted_count} suppressed={suppressed_count}")


def _infer_language(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "py": "python", "js": "javascript", "ts": "typescript", "tsx": "typescript",
        "jsx": "javascript", "go": "go", "java": "java", "kt": "kotlin",
        "swift": "swift", "rb": "ruby", "rs": "rust",
    }.get(ext, "unknown")


def _format_comment_body(candidate) -> str:
    category_emoji = {"bug": "🐛", "security": "🔒", "error_handling": "⚠️", "style": "🎨"}
    emoji = category_emoji.get(candidate.category, "💬")
    return f"{emoji} **{candidate.category}**: {candidate.comment_text}"


def _build_summary(*, files_reviewed, files_skipped, posted_count, suppressed_count) -> str:
    return (
        f"✅ Reviewed {files_reviewed} files ({files_skipped} skipped: lockfiles, "
        f"configs, or over the per-review cap).\n\n"
        f"Posted {posted_count} comment(s). {suppressed_count} candidate comment(s) "
        f"were filtered out as low-confidence, low-severity, or duplicate."
    )


if __name__ == "__main__":
    main()
