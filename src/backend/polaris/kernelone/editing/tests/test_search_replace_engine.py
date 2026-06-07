from __future__ import annotations

from polaris.kernelone.editing.search_replace_engine import (
    _replacer_chain_apply,
    apply_fuzzy_search_replace,
)


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


def test_apply_fuzzy_search_replace_block_anchor_fuzzy_middle() -> None:
    # ADR-0062: the OpenCode replacer chain is wired in as the precise tier
    # before the loose SequenceMatcher fallback. This case — exact first/last
    # line anchors with a *very different* middle line — is NOT matched by any
    # of the 10 precise strategies nor by SequenceMatcher (they return None),
    # but BlockAnchorReplacer anchors on the boundaries and resolves a unique
    # block. Wiring the chain turns this previously-unhandled edit into a hit.
    content = "START\nfoo bar baz qux\nEND\n"
    updated = apply_fuzzy_search_replace(
        content=content,
        search="START\ncompletely different middle line\nEND",
        replace="REPLACED",
    )
    assert updated == "REPLACED"


def test_replacer_chain_apply_requires_unique_candidate() -> None:
    # The wired tier must never silently edit an ambiguous location: when a
    # replacer's candidate occurs more than once in the content, it is skipped.
    content = "dup\ndup\n"
    assert _replacer_chain_apply("dup\n", "X\n", content) is None


def test_replacer_chain_apply_returns_none_when_no_strategy_matches() -> None:
    assert _replacer_chain_apply("nonexistent", "X", "totally unrelated content") is None
