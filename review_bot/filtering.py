"""File filtering, ranking, and capping logic — decides which changed files
in a PR actually get sent to the LLM. See conversation history for full rationale.
"""

from fnmatch import fnmatch

EXCLUDE_PATH_PATTERNS = [
    "*package-lock.json", "*yarn.lock", "*poetry.lock", "*Gemfile.lock", "*go.sum",
    "*/vendor/*", "*/node_modules/*", "*.generated.*", "*_pb2.py",
    "*.pb.go", "*/dist/*", "*/build/*",
    "*.json", "*.yaml", "*.yml", "*.toml", "*.md", "*.txt",
    "*/migrations/*",
    "*/__snapshots__/*", "*/fixtures/*",
]

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".swift", ".rb", ".rs"
}

MAX_DIFF_LINES = 800
MAX_FILES_PER_REVIEW = 15

CATEGORY_WEIGHT = {"auth": 3, "payments": 3, "security": 3, "api": 2, "core": 2, "default": 1}


def infer_area(path: str) -> str:
    lower = path.lower()
    if any(kw in lower for kw in ["auth", "login", "session", "token"]):
        return "auth"
    if any(kw in lower for kw in ["payment", "billing", "charge"]):
        return "payments"
    if "/api/" in lower or lower.endswith("_handler.py") or lower.endswith("_controller.go"):
        return "api"
    return "default"


def should_skip_file(file_diff) -> tuple[bool, str | None]:
    """file_diff: a PyGithub File object (from github_client.get_changed_files)."""
    if file_diff.status == "removed":
        return True, "file_deleted"

    if any(fnmatch(file_diff.filename, pattern) for pattern in EXCLUDE_PATH_PATTERNS):
        return True, "excluded_path"

    ext = "." + file_diff.filename.rsplit(".", 1)[-1] if "." in file_diff.filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        return True, "unsupported_extension"

    if file_diff.additions == 0 and file_diff.deletions == 0:
        return True, "no_content_change"

    if file_diff.additions + file_diff.deletions > MAX_DIFF_LINES:
        return True, "diff_too_large"

    return False, None


def rank_files_for_review(files):
    def priority(f):
        return (-CATEGORY_WEIGHT.get(infer_area(f.filename), 0), -(f.additions + f.deletions))
    return sorted(files, key=priority)


def filter_and_cap_files(all_files):
    """Returns (files_to_review, skips) where skips is a list of (file_path, reason)
    covering both hard-excluded files and files cut by the per-review cap.
    """
    skips: list[tuple[str, str]] = []
    candidates = []

    for f in all_files:
        skip, reason = should_skip_file(f)
        if skip:
            skips.append((f.filename, reason))
        else:
            candidates.append(f)

    ranked = rank_files_for_review(candidates)
    files_to_review = ranked[:MAX_FILES_PER_REVIEW]
    capped_out = ranked[MAX_FILES_PER_REVIEW:]
    skips.extend((f.filename, "capped_out") for f in capped_out)

    return files_to_review, skips
