"""Shared GitHub API helpers: diff fetching, comment posting, reaction fetching.

Uses PyGithub. Requires GITHUB_TOKEN in the environment (GitHub Actions provides
this automatically via secrets.GITHUB_TOKEN, passed through in the workflow YAML).
"""

import os
from github import Github
from github.GithubException import GithubException


def get_repo():
    gh = Github(os.environ["GITHUB_TOKEN"])
    return gh.get_repo(os.environ["REPO"])


def get_pr(repo, pr_number: int):
    return repo.get_pull(pr_number)


def get_changed_files(pr):
    """Returns PyGithub File objects for all files changed in the PR.
    TODO: page through if pr.changed_files > 300 (GitHub API pagination limit per call).
    """
    return list(pr.get_files())


def get_diff_lines(file_diff) -> set[int]:
    """Parses a file's `patch` (unified diff text) and returns the set of line
    numbers (in the NEW file) that are actually part of the diff — i.e. valid
    targets for an inline review comment. GitHub rejects comments on lines
    outside this set.
    """
    if not file_diff.patch:
        return set()

    valid_lines = set()
    new_line_num = None

    for line in file_diff.patch.splitlines():
        if line.startswith("@@"):
            # e.g. "@@ -12,7 +15,9 @@ def foo():"  -> new file starts at line 15
            new_hunk_part = line.split("+")[1].split(" ")[0]
            new_line_num = int(new_hunk_part.split(",")[0])
        elif line.startswith("-"):
            continue  # removed line, doesn't exist in new file
        elif line.startswith("+"):
            valid_lines.add(new_line_num)
            new_line_num += 1
        else:
            # context line, present in both old and new
            if new_line_num is not None:
                new_line_num += 1

    return valid_lines


def post_review(pr, *, commit_sha: str, accepted_comments: list[dict]) -> dict[int, str]:
    """Posts a batch of inline comments as a single PR review.

    accepted_comments: list of dicts, each with keys:
        internal_id   -- your local `comments.id` (int), used only for mapping the
                          response back
        file_path     -- str
        line_number   -- int (must be a valid diff line — see get_diff_lines)
        body          -- str (the comment text to post)

    Returns: dict mapping internal_id -> github_comment_id (str), for the ones
    that were successfully posted. Comments that GitHub rejects (e.g. invalid
    line) are omitted from the return value and should be logged as failures,
    not silently dropped.
    """
    if not accepted_comments:
        return {}

    review_comments = [
        {
            "path": c["file_path"],
            "line": c["line_number"],
            "side": "RIGHT",
            "body": c["body"],
        }
        for c in accepted_comments
    ]

    try:
        review = pr.create_review(
            commit=_get_commit(pr, commit_sha),
            body="🤖 AI review — see inline comments below.",
            event="COMMENT",
            comments=review_comments,
        )
    except GithubException as e:
        # Whole-batch failure (e.g. bad commit_sha, permissions issue). Let main.py
        # decide how to handle: log and stop, or retry without the offending comment.
        raise RuntimeError(f"Failed to post review: {e.data}") from e

    # PullRequestReview (the object create_review returns) doesn't expose its
    # own comments in PyGithub — there's no review.get_comments(). Instead,
    # fetch all review comments on the PR and filter down to the ones that
    # belong to the review we just created, matched via pull_request_review_id.
    posted_by_location = {}
    for rc in pr.get_review_comments():
        if getattr(rc, "pull_request_review_id", None) != review.id:
            continue
        posted_by_location[(rc.path, rc.original_line)] = str(rc.id)

    result = {}
    for c in accepted_comments:
        key = (c["file_path"], c["line_number"])
        if key in posted_by_location:
            result[c["internal_id"]] = posted_by_location[key]

    return result


def _get_commit(pr, commit_sha: str):
    return pr.base.repo.get_commit(commit_sha)


def post_summary_comment(pr, *, body: str):
    """Posts a plain (non-inline) issue comment, e.g. the file-coverage summary
    ("reviewed 15 of 27 files...") or a failure notice.
    """
    pr.as_issue().create_comment(body)


def get_review_comment(repo, github_comment_id: int):
    return repo.get_pull_request_review_comment(github_comment_id)


def get_reactions(review_comment):
    """Returns the reactions on a given review comment."""
    return list(review_comment.get_reactions())