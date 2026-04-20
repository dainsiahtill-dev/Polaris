from __future__ import annotations

from polaris.kernelone.editing.search_replace_engine import apply_fuzzy_search_replace


def test_apply_fuzzy_search_replace_exact_window() -> None:
    content = "def old_name():\n    return 1\n"
    updated = apply_fuzzy_search_replace(
        content=content,
        search="def old_name():\n    return 1\n",
        replace="def new_name():\n    return 2\n",
    )
    assert updated is not None
    assert "new_name" in updated


def test_apply_fuzzy_search_replace_whitespace_tolerant() -> None:
    content = "def old_name():\n  return 1\n"
    updated = apply_fuzzy_search_replace(
        content=content,
        search="def old_name():\n    return 1\n",
        replace="def new_name():\n    return 2\n",
    )
    assert updated is not None
    assert "def new_name()" in updated


def test_apply_fuzzy_search_replace_dotdot_ellipsis() -> None:
    content = "a = 1\nb = 2\nc = 3\n"
    updated = apply_fuzzy_search_replace(
        content=content,
        search="a = 1\n...\nc = 3\n",
        replace="a = 10\n...\nc = 30\n",
    )
    assert updated is not None
    assert "a = 10" in updated
    assert "c = 30" in updated


def test_apply_fuzzy_search_replace_leading_whitespace_offset() -> None:
    content = "    def old_name():\n        return 1\n"
    updated = apply_fuzzy_search_replace(
        content=content,
        search="def old_name():\n    return 1\n",
        replace="def new_name():\n    return 2\n",
    )
    assert updated is not None
    assert "def new_name()" in updated
    assert "return 2" in updated
