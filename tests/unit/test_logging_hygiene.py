"""No log call may carry health content (YGH-90).

The rule is: a log line holds ids, counts, durations, statuses and the error CLASS; never a
question, an answer, a quote, a claim, evidence text, an email, a token, or an exception's text (an
exception raised while handling a record can quote it). Convention alone drifts, and this
repository had already logged claim text once, so the source is scanned: every logging call under
services/ and packages/ is parsed and its arguments are checked for names that carry content.

A call that must name one of these (it should not) can end its line with `# log-ok: <why>`.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("services", "packages")
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
LOGGER_NAMES = {"logger", "log", "logging", "_logger"}
# Names that hold health content or credentials.
CONTENT_NAMES = {
    "question", "answer", "quote", "claim", "claims", "claim_text", "flag_text", "text", "prompt",
    "content", "body", "evidence", "email", "password", "token", "explanation", "uncertainty",
    "raw", "message", "messages", "payload", "data",
}
# A caught exception's text can quote what it was handling: log type(exc).__name__ instead.
EXCEPTION_NAMES = {"exc", "e", "err", "error", "ex"}
RISKY = CONTENT_NAMES | EXCEPTION_NAMES


def python_files():
    for folder in SCANNED:
        for path in (ROOT / folder).rglob("*.py"):
            if "migrations" not in path.parts:
                yield path


def _names_in(node: ast.AST) -> set:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _is_exception_class_name(node: ast.AST) -> bool:
    """type(exc).__name__ is fine: it is the class, not the text."""
    return (isinstance(node, ast.Attribute) and node.attr == "__name__"
            and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "type")


def is_log_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS and isinstance(node.func.value, ast.Name)
            and node.func.value.id in LOGGER_NAMES)


def risky_names(call: ast.Call) -> set:
    """The content-bearing names a log call's arguments (after the message) refer to."""
    found = set()
    for arg in list(call.args[1:]) + [kw.value for kw in call.keywords if kw.arg != "exc_info"]:
        if _is_exception_class_name(arg):
            continue
        if isinstance(arg, ast.Dict):  # extra={...}: judge the keys and the values
            names = {k.value for k in arg.keys if isinstance(k, ast.Constant)}
            for value in arg.values:
                if not _is_exception_class_name(value):
                    names |= _names_in(value)
        else:
            names = _names_in(arg)
        found |= names & RISKY
    return found


def violations() -> list:
    found = []
    for path in python_files():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not is_log_call(node):
                continue
            if "log-ok" in lines[node.lineno - 1] or "log-ok" in lines[node.end_lineno - 1]:
                continue
            hit = risky_names(node)
            if hit:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno} logs {sorted(hit)}")
    return found


def test_no_log_call_carries_health_content_or_an_exceptions_text():
    assert violations() == [], "\n" + "\n".join(violations())


# --- the scanner itself must catch what it is for ---------------------------------------------
def scan(snippet: str) -> set:
    calls = [n for n in ast.walk(ast.parse(snippet)) if is_log_call(n)]
    assert calls, "the snippet has no log call"
    return set().union(*[risky_names(c) for c in calls])


@pytest.mark.parametrize("snippet", [
    'logger.warning("flagged %s", flag_text)',
    'logger.info("asked %s", question)',
    'logger.info("got", extra={"answer": a})',
    'logger.info("got", extra={"x": claim.text})',
    'logger.exception("failed: %s", exc)',
    'logger.error("failed", extra={"detail": str(exc)})',
    'logger.warning("email %s", user.email)',
    'logger.info("body %s", payload)',
    'logger.info("x", extra={"data": 1})',
])
def test_the_scanner_catches_content_in_log_calls(snippet):
    assert scan(snippet), snippet


@pytest.mark.parametrize("snippet", [
    'logger.info("done", extra={"count": n, "duration_ms": ms})',
    'logger.error("failed", extra={"error_type": type(exc).__name__})',
    'logger.warning("document %s failed at %s", document_id, stage)',
    'logger.info("started")',
    'logger.exception("crashed")',
])
def test_the_scanner_lets_metadata_through(snippet):
    assert scan(snippet) == set(), snippet


def test_the_override_comment_is_honoured_and_the_scan_covers_the_code_that_exists():
    assert any(path.name == "verify.py" for path in python_files())
    assert any(path.name == "providers.py" for path in python_files())
    assert sum(1 for _ in python_files()) > 40
