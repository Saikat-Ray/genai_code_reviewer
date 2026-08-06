"""Enclosing-function context extraction via tree-sitter.

Given a file's content and a changed line number, walks the AST up from that
line to find the nearest enclosing function/method, and extracts:
  - the full text of that function (or a truncated window if it's very large)
  - one-line signatures of any same-file functions it calls, so the generator
    has a rough idea what those calls do without paying for their full bodies

Falls back to a fixed line-window when no enclosing function is found (e.g.
module-level code) or when the language isn't supported.
"""

from dataclasses import dataclass, field

from tree_sitter_languages import get_parser

MAX_FUNCTION_LINES = 150
TRUNCATE_WINDOW = 30
FALLBACK_WINDOW = 15

# Node type names, per tree-sitter grammar, that count as "a function" to walk
# up to. Grammars vary in what they call things, so this is language-specific.
FUNCTION_NODE_TYPES = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "typescript": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "tsx": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
    "kotlin": {"function_declaration"},
    "ruby": {"method", "singleton_method"},
    "rust": {"function_item"},
}

# Node type for "this is a function call" per grammar, used when scanning the
# enclosing function's body for same-file callees.
CALL_NODE_TYPES = {
    "python": {"call"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "tsx": {"call_expression"},
    "go": {"call_expression"},
    "java": {"method_invocation"},
    "kotlin": {"call_expression"},
    "ruby": {"call", "method_call"},
    "rust": {"call_expression"},
}

# tree-sitter-languages doesn't ship a grammar for Swift; anything not in
# FUNCTION_NODE_TYPES automatically falls back to the line-window approach.
SUPPORTED_LANGUAGES = set(FUNCTION_NODE_TYPES.keys())


@dataclass
class CodeContext:
    enclosing_function_text: str
    related_signatures: list[str] = field(default_factory=list)
    start_line: int = 0  # 1-indexed, inclusive
    end_line: int = 0    # 1-indexed, inclusive
    used_fallback: bool = False


def get_enclosing_function(file_content: str, language: str, line_number: int) -> CodeContext:
    """line_number is 1-indexed, matching GitHub's line numbering."""
    if language not in SUPPORTED_LANGUAGES:
        return fallback_line_window(file_content, line_number)

    try:
        parser = get_parser(language)
        tree = parser.parse(bytes(file_content, "utf8"))
    except Exception:
        # Malformed file, encoding issue, or grammar hiccup — don't let context
        # extraction take down the whole review for one bad file.
        return fallback_line_window(file_content, line_number)

    target_row = line_number - 1  # tree-sitter rows are 0-indexed
    node = tree.root_node.descendant_for_point_range((target_row, 0), (target_row, 0))
    if node is None:
        return fallback_line_window(file_content, line_number)

    func_node = _walk_up_to_function(node, FUNCTION_NODE_TYPES[language])
    if func_node is None:
        return fallback_line_window(file_content, line_number)

    start_line = func_node.start_point[0] + 1
    end_line = func_node.end_point[0] + 1
    lines = file_content.splitlines()

    if end_line - start_line > MAX_FUNCTION_LINES:
        window_start = max(start_line, line_number - TRUNCATE_WINDOW)
        window_end = min(end_line, line_number + TRUNCATE_WINDOW)
        text = "\n".join(lines[window_start - 1:window_end])
        return CodeContext(
            enclosing_function_text=text,
            related_signatures=[],  # skip callee lookup on truncated extracts — not worth the noise
            start_line=window_start,
            end_line=window_end,
        )

    text = "\n".join(lines[start_line - 1:end_line])
    signatures = _find_same_file_callee_signatures(tree, func_node, file_content, language)

    return CodeContext(
        enclosing_function_text=text,
        related_signatures=signatures,
        start_line=start_line,
        end_line=end_line,
    )


def fallback_line_window(file_content: str, line_number: int, window: int = FALLBACK_WINDOW) -> CodeContext:
    lines = file_content.splitlines()
    start = max(1, line_number - window)
    end = min(len(lines), line_number + window)
    return CodeContext(
        enclosing_function_text="\n".join(lines[start - 1:end]),
        related_signatures=[],
        start_line=start,
        end_line=end,
        used_fallback=True,
    )


def _walk_up_to_function(node, function_types: set[str]):
    current = node
    while current is not None:
        if current.type in function_types:
            return current
        current = current.parent
    return None


def _find_same_file_callee_signatures(tree, func_node, file_content: str, language: str,
                                       max_signatures: int = 8) -> list[str]:
    """Best-effort: find names called within func_node, then look for matching
    top-level function definitions elsewhere in the file and return their
    signature line only (not the body). Cheap context that helps the generator
    avoid guessing what a helper function does.
    """
    call_types = CALL_NODE_TYPES.get(language, set())
    if not call_types:
        return []

    called_names = set()
    _collect_call_names(func_node, call_types, called_names)
    if not called_names:
        return []

    func_types = FUNCTION_NODE_TYPES[language]
    lines = file_content.splitlines()
    signatures = []

    for node in _walk_all(tree.root_node):
        if node.type not in func_types or node == func_node:
            continue
        name = _get_function_name(node)
        if name in called_names:
            sig_line = node.start_point[0]
            signatures.append(lines[sig_line].strip())
        if len(signatures) >= max_signatures:
            break

    return signatures


def _collect_call_names(node, call_types: set[str], out: set[str]):
    if node.type in call_types:
        # The callee is typically the first named child (function/identifier being called).
        callee = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
        if callee is not None:
            out.add(callee.text.decode("utf8", errors="ignore").split(".")[-1])
    for child in node.children:
        _collect_call_names(child, call_types, out)


def _get_function_name(func_node) -> str | None:
    name_node = func_node.child_by_field_name("name")
    if name_node is not None:
        return name_node.text.decode("utf8", errors="ignore")
    return None


def _walk_all(node):
    yield node
    for child in node.children:
        yield from _walk_all(child)
