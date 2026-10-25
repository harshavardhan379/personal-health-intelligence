"""Marks every test by the directory it lives in, so a suite can be picked with `-m`
(`pytest -m safety`, `pytest -m "not integration"`) without decorating each file."""

import pytest

_BY_DIRECTORY = {
    "unit": ("unit",),
    "safety": ("safety",),
    "evaluation": ("benchmark",),
    "regression": ("regression", "integration"),
    "integration": ("integration",),
}


def pytest_collection_modifyitems(items):
    for item in items:
        parts = item.path.parts
        if "tests" not in parts:
            continue
        directory = parts[parts.index("tests") + 1] if len(parts) > parts.index("tests") + 2 else ""
        for marker in _BY_DIRECTORY.get(directory, ()):
            item.add_marker(getattr(pytest.mark, marker))
