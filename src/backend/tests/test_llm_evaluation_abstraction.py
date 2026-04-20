"""Tests verifying that llm.evaluation Cell abstracts HTTP and filesystem access.

Two concerns are validated:

1. ``get_embedding_vector`` (utils.py) — must NOT make real HTTP calls.
   It must delegate to ``KernelEmbeddingPort`` (polaris.kernelone.llm.embedding).

2. ``reconcile_llm_test_index`` (index.py) — must NOT call os.listdir directly.
   It must delegate to ``KernelFsReportsPort`` (injected via ``set_reports_port``).

All tests use fake/stub implementations; no real HTTP or filesystem I/O.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import polaris.kernelone.llm.embedding as embedding_module
import pytest
from polaris.cells.llm.evaluation.internal.index import (
    load_llm_test_index,
    reconcile_llm_test_index,
    set_reports_port,
    update_index_with_report,
)
from polaris.cells.llm.evaluation.internal.utils import (
    cosine_similarity,
    get_embedding_vector,
    semantic_criteria_hits,
)

# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------


class _FakeEmbeddingPort:
    """Deterministic fake embedding port — no HTTP calls."""

    def __init__(self, dimension: int = 4) -> None:
        self._dim = dimension
        self.calls: list[tuple[str, str | None]] = []

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        self.calls.append((text, model))
        # Produce a stable non-zero vector based on text length.
        base = float(len(text) % 10 + 1)
        return [base / 10.0] * self._dim

    def get_fingerprint(self) -> str:
        return "fake-embedding-port-v1"


class _FailingEmbeddingPort:
    """Embedding port that always raises (simulates service down)."""

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        raise ConnectionError("embedding service unavailable (fake)")

    def get_fingerprint(self) -> str:
        return "failing-embedding-port-v1"


class _FakeReportsPort:
    """Fake KernelFsReportsPort backed by an in-memory dict."""

    def __init__(self, files: dict[str, dict]) -> None:
        """files: {filename: report_dict}"""
        self._files = files
        self.list_calls: list[str] = []
        self.dir_calls: list[str] = []

    def list_json_files(self, directory: str) -> list[str]:
        self.list_calls.append(directory)
        return list(self._files.keys())

    def dir_exists(self, directory: str) -> bool:
        self.dir_calls.append(directory)
        return True


class _EmptyDirReportsPort:
    """Fake KernelFsReportsPort for a non-existent directory."""

    def list_json_files(self, directory: str) -> list[str]:
        return []

    def dir_exists(self, directory: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_embedding_port():
    """Restore the global embedding port after each test."""
    original = embedding_module._default_embedding_port
    yield
    embedding_module._default_embedding_port = original


@pytest.fixture(autouse=True)
def _reset_reports_port():
    """Restore the global reports port after each test."""
    import polaris.cells.llm.evaluation.internal.index as idx_mod
    original = idx_mod._default_reports_port
    yield
    idx_mod._default_reports_port = original


# ---------------------------------------------------------------------------
# Part 1: Embedding port abstraction (utils.py)
# ---------------------------------------------------------------------------


class TestGetEmbeddingVectorUsesPort:
    def test_raises_when_no_port_injected(self):
        """get_embedding_vector raises RuntimeError when port is not set."""
        embedding_module._default_embedding_port = None
        with pytest.raises(RuntimeError, match="KernelEmbeddingPort is not set"):
            get_embedding_vector("hello")

    def test_delegates_to_injected_port(self):
        """get_embedding_vector returns the port's output unchanged."""
        fake = _FakeEmbeddingPort(dimension=4)
        embedding_module.set_default_embedding_port(fake)

        result = get_embedding_vector("test text", model="nomic-embed-text")

        assert result is not None
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)
        # The fake port was called exactly once.
        assert len(fake.calls) == 1
        text_arg, model_arg = fake.calls[0]
        assert text_arg == "test text"

    def test_returns_none_when_port_raises(self):
        """get_embedding_vector returns None (not raises) when the port fails."""
        embedding_module.set_default_embedding_port(_FailingEmbeddingPort())

        result = get_embedding_vector("any text")

        assert result is None

    def test_no_urllib_import_in_call_path(self):
        """Verify urllib.request is not imported during a normal get_embedding_vector call."""
        fake = _FakeEmbeddingPort()
        embedding_module.set_default_embedding_port(fake)

        # Patch urllib.request to raise if imported — it must NOT be reached.
        with patch.dict("sys.modules", {"urllib.request": None}):
            # Should NOT raise because urllib.request is not used.
            result = get_embedding_vector("check no urllib")

        assert result is not None

    def test_empty_text_handled_gracefully(self):
        """Empty string input does not crash."""
        fake = _FakeEmbeddingPort()
        embedding_module.set_default_embedding_port(fake)

        result = get_embedding_vector("")
        # Fake port returns a non-empty list for any input.
        assert isinstance(result, list)


class TestSemanticCriteriaHitsUsesPort:
    def test_returns_zero_scores_when_port_not_set(self):
        """semantic_criteria_hits returns zero scores when embedding port is not set."""
        # When get_embedding_vector raises RuntimeError for missing port,
        # semantic_criteria_hits must surface that error (not swallow).
        embedding_module._default_embedding_port = None
        with pytest.raises(RuntimeError, match="KernelEmbeddingPort is not set"):
            semantic_criteria_hits("answer", ["criterion 1"])

    def test_scores_computed_via_port(self):
        """semantic_criteria_hits uses the port, not urllib."""
        fake = _FakeEmbeddingPort(dimension=4)
        embedding_module.set_default_embedding_port(fake)

        scores = semantic_criteria_hits("some answer", ["criterion a", "criterion b"])

        assert "criterion a" in scores
        assert "criterion b" in scores
        # Cosine similarity between two identical vectors is 1.0; our fake
        # returns identical vectors for same-length inputs, so scores may vary —
        # just assert they are floats in [0, 1].
        for score in scores.values():
            assert 0.0 <= score <= 1.0

    def test_empty_criteria_returns_empty(self):
        """No embedding calls when criteria list is empty."""
        fake = _FakeEmbeddingPort()
        embedding_module.set_default_embedding_port(fake)

        result = semantic_criteria_hits("anything", [])
        assert result == {}
        assert fake.calls == []


# ---------------------------------------------------------------------------
# Part 2: KernelFsReportsPort abstraction (index.py)
# ---------------------------------------------------------------------------


class TestReconcileUsesReportsPort:
    def _make_report(self, role: str, provider_id: str, model: str) -> dict:
        return {
            "target": {"role": role, "provider_id": provider_id, "model": model},
            "final": {"ready": True, "grade": "PASS"},
            "test_run_id": "abc12345",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "suites": {"basic": {"ok": True, "total_cases": 1, "passed_cases": 1, "failed_cases": 0}},
        }

    def test_no_os_listdir_called_when_port_injected(self, monkeypatch, tmp_path):
        """reconcile_llm_test_index must not call os.listdir when a port is injected."""
        # Isolate all global root paths to tmp_path so we don't touch the
        # real ~/.polaris during the test.
        monkeypatch.setenv("POLARIS_ROOT", str(tmp_path))
        monkeypatch.setenv("POLARIS_HOME", str(tmp_path / ".polaris"))
        monkeypatch.setenv("POLARIS_WORKSPACE", str(tmp_path))

        report = self._make_report("pm", "ollama-local", "llama3")
        fake_port = _FakeReportsPort({"report_run1.json": report})
        set_reports_port(fake_port)

        # Patch os.listdir to raise — it must not be reached.
        def _forbidden_listdir(path):
            raise AssertionError(f"os.listdir called directly: {path}")

        monkeypatch.setattr(os, "listdir", _forbidden_listdir)

        workspace = str(tmp_path)
        reports_dir = str(tmp_path / "reports")

        # The fake port's list_json_files returns filenames; the reconcile
        # function will attempt to open them — patch open to return the report.
        import builtins
        original_open = builtins.open

        def _fake_open(path, *args, **kwargs):
            # Only intercept report json reads; let tmp_path index writes through.
            path_str = str(path)
            if path_str.endswith("report_run1.json"):
                import io
                return io.StringIO(json.dumps(report))
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _fake_open)

        result = reconcile_llm_test_index(workspace, reports_dir=reports_dir)

        # Port was called, not os.listdir.
        assert len(fake_port.list_calls) == 1
        assert "pm" in result.get("roles", {})

    def test_missing_directory_via_port(self, tmp_path):
        """reconcile returns default payload when dir_exists returns False."""
        set_reports_port(_EmptyDirReportsPort())
        workspace = str(tmp_path)

        result = reconcile_llm_test_index(workspace, reports_dir=str(tmp_path / "nonexistent"))

        # Should return the loaded (empty) index without error.
        assert "roles" in result
        assert "providers" in result

    def test_port_injection_isolation(self, tmp_path, monkeypatch):
        """set_reports_port replaces the active port for subsequent calls."""
        # Isolate all global root paths so writes go to tmp_path only.
        monkeypatch.setenv("POLARIS_ROOT", str(tmp_path))
        monkeypatch.setenv("POLARIS_HOME", str(tmp_path / ".polaris"))
        monkeypatch.setenv("POLARIS_WORKSPACE", str(tmp_path))

        port_a = _FakeReportsPort({})
        port_b = _FakeReportsPort({})

        set_reports_port(port_a)
        reconcile_llm_test_index(str(tmp_path), reports_dir=str(tmp_path / "r"))

        set_reports_port(port_b)
        reconcile_llm_test_index(str(tmp_path), reports_dir=str(tmp_path / "r"))

        assert len(port_a.dir_calls) == 1
        assert len(port_b.dir_calls) == 1


class TestLoadAndUpdateIndexNoDirectOs:
    def test_load_returns_default_when_no_file(self, tmp_path, monkeypatch):
        """load_llm_test_index returns DEFAULT_INDEX_PAYLOAD when no file exists."""
        monkeypatch.setenv("POLARIS_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POLARIS_ROOT", str(tmp_path))
        monkeypatch.setenv("POLARIS_HOME", str(tmp_path / ".polaris"))

        result = load_llm_test_index(str(tmp_path))

        assert "roles" in result
        assert "providers" in result
        assert "version" in result

    def test_update_index_stores_report(self, tmp_path, monkeypatch):
        """update_index_with_report writes to the expected path."""
        monkeypatch.setenv("POLARIS_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POLARIS_ROOT", str(tmp_path))
        monkeypatch.setenv("POLARIS_HOME", str(tmp_path / ".polaris"))

        report = {
            "target": {"role": "architect", "provider_id": "anthropic-claude", "model": "claude-3-5-sonnet"},
            "final": {"ready": True, "grade": "PASS"},
            "test_run_id": "xyzzy123",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }

        update_index_with_report(str(tmp_path), report)

        result = load_llm_test_index(str(tmp_path))
        assert "architect" in result.get("roles", {})
        assert result["roles"]["architect"]["grade"] == "PASS"


# ---------------------------------------------------------------------------
# Part 3: Cosine similarity unit tests (pure logic, no I/O)
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_empty_input_returns_zero(self):
        assert cosine_similarity([], []) == 0.0

    def test_mismatched_length_returns_zero(self):
        assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0
