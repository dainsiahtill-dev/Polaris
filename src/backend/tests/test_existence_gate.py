"""
tests/test_existence_gate.py — Unit tests for existence_gate.check_mode().

All tests use real filesystem (tempfile) — no mocks needed since the module
has zero LLM calls and zero side-effects.
"""

from __future__ import annotations

import os
import sys
import tempfile

# Make core module importable from tests directory.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"),
)

from polaris.cells.director.execution.internal.existence_gate import (
    check_mode,
    is_any_missing,
    is_pure_create,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(existing_rels: list[str]) -> str:
    """Return a temp dir that has *existing_rels* as empty files."""
    tmp = tempfile.mkdtemp()
    for rel in existing_rels:
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
    return tmp


# ---------------------------------------------------------------------------
# Pure-create scenarios
# ---------------------------------------------------------------------------

class TestCreateMode:
    def test_all_missing_returns_create(self):
        ws = _make_workspace([])
        result = check_mode(["src/index.ts", "src/utils.ts"], ws)
        assert result.mode == "create"
        assert result.missing_count == 2
        assert result.existing_count == 0
        assert is_pure_create(result)

    def test_single_missing_returns_create(self):
        ws = _make_workspace([])
        result = check_mode(["newfile.py"], ws)
        assert result.mode == "create"

    def test_empty_targets_returns_modify(self):
        ws = _make_workspace([])
        result = check_mode([], ws)
        assert result.mode == "modify"
        assert result.target_total == 0


# ---------------------------------------------------------------------------
# Pure-modify scenarios
# ---------------------------------------------------------------------------

class TestModifyMode:
    def test_all_existing_returns_modify(self):
        ws = _make_workspace(["src/index.ts", "src/utils.ts"])
        result = check_mode(["src/index.ts", "src/utils.ts"], ws)
        assert result.mode == "modify"
        assert result.existing_count == 2
        assert result.missing_count == 0
        assert not is_any_missing(result)

    def test_single_existing_returns_modify(self):
        ws = _make_workspace(["app.py"])
        result = check_mode(["app.py"], ws)
        assert result.mode == "modify"


# ---------------------------------------------------------------------------
# Mixed scenarios
# ---------------------------------------------------------------------------

class TestMixedMode:
    def test_some_missing_returns_mixed(self):
        ws = _make_workspace(["src/existing.ts"])
        result = check_mode(["src/existing.ts", "src/new.ts"], ws)
        assert result.mode == "mixed"
        assert result.existing_count == 1
        assert result.missing_count == 1
        assert is_any_missing(result)
        assert not is_pure_create(result)


# ---------------------------------------------------------------------------
# Hint override
# ---------------------------------------------------------------------------

class TestHintOverride:
    def test_create_hint_overrides_existing_files(self):
        ws = _make_workspace(["src/app.py"])
        result = check_mode(["src/app.py"], ws, mode_hint="create")
        assert result.mode == "create"
        # Existence data is still computed.
        assert result.existing_count == 1

    def test_modify_hint_overrides_missing_files(self):
        ws = _make_workspace([])
        result = check_mode(["src/missing.py"], ws, mode_hint="modify")
        assert result.mode == "modify"
        assert result.missing_count == 1

    def test_auto_hint_falls_through_to_detection(self):
        ws = _make_workspace([])
        result = check_mode(["src/missing.py"], ws, mode_hint="auto")
        assert result.mode == "create"


# ---------------------------------------------------------------------------
# as_dict / repr
# ---------------------------------------------------------------------------

class TestGateResult:
    def test_as_dict_has_all_keys(self):
        ws = _make_workspace(["a.py"])
        result = check_mode(["a.py", "b.py"], ws)
        d = result.as_dict()
        assert "mode" in d
        assert "target_total" in d
        assert "existing_count" in d
        assert "missing_count" in d
        assert "existing" in d
        assert "missing" in d
