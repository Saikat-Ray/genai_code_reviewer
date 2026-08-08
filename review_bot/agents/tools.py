"""Dependency vulnerability lookup, used by any agent configured with
use_vulnerability_tool=True (see agents/config.py). Deterministic: scan
results are computed directly from the diff and injected into the agent's
prompt as context, rather than left to the model to decide whether to call a
tool — more reliable, since it doesn't depend on the model remembering to ask.

A real langchain @tool wrapper is still provided (check_package_vulnerability)
so it's usable in a true tool-calling/ReAct loop later if you want the model
to decide when to invoke it — e.g. for follow-up queries about a specific
package it's unsure about.
"""

import re
from pathlib import Path

from langchain_core.tools import tool

DEPENDENCY_FILE_ECOSYSTEMS = {
    "requirements.txt": "PyPI", "pyproject.toml": "PyPI", "poetry.lock": "PyPI",
    "package.json": "npm", "package-lock.json": "npm", "yarn.lock": "npm",
    "go.mod": "Go", "go.sum": "Go",
    "Gemfile": "RubyGems", "Gemfile.lock": "RubyGems",
    "pom.xml": "Maven", "build.gradle": "Maven",
}

_DEP_LINE_PATTERNS = {
    "PyPI": re.compile(r'^\+?\s*([A-Za-z0-9_.\-]+)\s*[=<>!~]+\s*([0-9][A-Za-z0-9.\-]*)'),
    "npm": re.compile(r'^\+?\s*"([A-Za-z0-9_.@/\-]+)"\s*:\s*"[\^~]?([0-9][A-Za-z0-9.\-]*)"'),
    "Go": re.compile(r'^\+?\s*([A-Za-z0-9_./\-]+)\s+v?([0-9][A-Za-z0-9.\-]*)'),
}


def is_dependency_file(file_path: str) -> bool:
    return Path(file_path).name in DEPENDENCY_FILE_ECOSYSTEMS


@tool
def check_package_vulnerability(ecosystem: str, package_name: str, version: str = "") -> str:
    """Look up known vulnerabilities for a package/version via the OSV.dev database.

    ecosystem: one of 'PyPI', 'npm', 'Go', 'RubyGems', 'Maven'.
    package_name: the package/library name as it appears in the manifest.
    version: exact version string if known, else leave blank.
    """
    import requests

    try:
        payload = {"package": {"name": package_name, "ecosystem": ecosystem}}
        if version:
            payload["version"] = version
        resp = requests.post("https://api.osv.dev/v1/query", json=payload, timeout=10)
        resp.raise_for_status()
        vulns = resp.json().get("vulns", [])
    except Exception as e:
        return f"Vulnerability lookup failed for {package_name}: {e}"

    if not vulns:
        return f"No known vulnerabilities found for {package_name} {version} ({ecosystem})."

    ids = ", ".join(v.get("id", "?") for v in vulns[:5])
    plural = "y" if len(vulns) == 1 else "ies"
    return (f"⚠️ Found {len(vulns)} known vulnerabilit{plural} for {package_name} "
            f"{version} ({ecosystem}): {ids}")


def scan_dependency_changes(file_path: str, diff_hunk: str) -> str:
    """Extracts added/changed dependency lines from a manifest diff, checks each
    against OSV.dev, and returns a human-readable summary block ready to inject
    into an agent's prompt. Returns "" if the file isn't a recognized manifest
    or no dependency lines were found — callers should skip adding this to the
    prompt in that case rather than injecting an empty section.
    """
    filename = Path(file_path).name
    ecosystem = DEPENDENCY_FILE_ECOSYSTEMS.get(filename)
    if ecosystem is None or ecosystem not in _DEP_LINE_PATTERNS:
        return ""

    pattern = _DEP_LINE_PATTERNS[ecosystem]
    deps = []
    for line in diff_hunk.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        match = pattern.match(line)
        if match:
            deps.append((match.group(1), match.group(2)))

    deps = deps[:10]  # cap to avoid excessive API calls on a huge lockfile diff
    if not deps:
        return ""

    results = [
        check_package_vulnerability.invoke({"ecosystem": ecosystem, "package_name": pkg, "version": ver})
        for pkg, ver in deps
    ]
    return "Dependency vulnerability scan results:\n" + "\n".join(results)
