from polaris.cells.roles.scout.internal.ports import FakeReadTool, canonical_args_key
from polaris.cells.roles.scout.internal.retrieval import retrieve


def test_retrieve_parses_rg_results_into_findings() -> None:
    args = {"pattern": "pay", "max_results": 40}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(args)): {
                "ok": True,
                "results": [{"file": "a.py", "line": 3, "snippet": "def pay():"}],
            },
        }
    )
    findings, coverage = retrieve(fake, [("repo_rg", args)])
    assert findings[0].path == "a.py"
    assert findings[0].line == 3
    assert findings[0].snippet == "def pay():"
    assert coverage["tools_used"] == ["repo_rg"]
    assert coverage["truncated"] is False


def test_retrieve_unwraps_nested_result_envelope() -> None:
    args = {"pattern": "pay", "max_results": 40}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(args)): {
                "ok": True,
                "result": {"results": [{"file": "a.py", "line": 1, "snippet": "pay"}]},
            },
        }
    )
    findings, _ = retrieve(fake, [("repo_rg", args)])
    assert findings and findings[0].path == "a.py"


def test_retrieve_parses_symbols_index() -> None:
    args = {"paths": ["."], "max_results": 200}
    fake = FakeReadTool(
        {
            ("repo_symbols_index", canonical_args_key(args)): {
                "ok": True,
                "symbols": [{"file": "m.py", "line": 5, "name": "Widget", "kind": "class_definition"}],
            },
        }
    )
    findings, _ = retrieve(fake, [("repo_symbols_index", args)])
    assert findings[0].path == "m.py"
    assert findings[0].symbol == "Widget"
    assert "Widget" in findings[0].snippet


def test_retrieve_parses_repo_tree_entries_as_path_findings() -> None:
    args = {"path": ".", "depth": 2}
    fake = FakeReadTool(
        {
            ("repo_tree", canonical_args_key(args)): {
                "ok": True,
                "entries": [
                    {"path": "pkg", "type": "dir"},
                    {"path": "pkg/pay.py", "type": "file"},
                ],
            },
        }
    )
    findings, _ = retrieve(fake, [("repo_tree", args)])
    paths = [f.path for f in findings]
    assert "pkg/pay.py" in paths
    assert "pkg" not in paths  # directories are skipped


def test_retrieve_marks_truncation_and_survives_tool_error() -> None:
    ok_args = {"pattern": "x", "max_results": 40}
    bad_args = {"path": "bad", "depth": 2}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(ok_args)): {
                "ok": True,
                "results": [{"file": "b.py", "line": 1, "snippet": "x"}],
                "truncated": True,
            },
            ("repo_tree", canonical_args_key(bad_args)): {"ok": False, "error": "boom"},
        }
    )
    findings, coverage = retrieve(fake, [("repo_rg", ok_args), ("repo_tree", bad_args)])
    assert coverage["truncated"] is True
    assert any("repo_tree" in e for e in coverage["errors"])
    assert len(findings) == 1


# ── successful-call accounting and path normalization ──


def test_retrieve_counts_successful_calls() -> None:
    """The coverage must state how many reads were actually permitted."""
    ok_args = {"pattern": "x", "max_results": 40}
    bad_args = {"path": "bad", "depth": 2}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(ok_args)): {"ok": True, "results": []},
            ("repo_tree", canonical_args_key(bad_args)): {"ok": False, "error": "boom"},
        }
    )
    _findings, coverage = retrieve(fake, [("repo_rg", ok_args), ("repo_tree", bad_args)])
    assert coverage["successful_calls"] == 1
    assert len(coverage["errors"]) == 1


def test_a_successful_read_with_zero_hits_is_not_a_denial() -> None:
    """Zero hits from a permitted read is a success, not a denied read."""
    args = {"pattern": "x", "max_results": 40}
    fake = FakeReadTool({("repo_rg", canonical_args_key(args)): {"ok": True, "results": []}})
    _findings, coverage = retrieve(fake, [("repo_rg", args)])
    assert coverage["successful_calls"] == 1
    assert coverage["errors"] == []


def test_retrieve_rebases_repo_tree_entries_onto_the_queried_root() -> None:
    """``repo_tree`` entries are relative to the queried directory.

    A multi-root probe would otherwise report a bare file name that could be
    mistaken for a workspace-root path.
    """
    args = {"path": "alpha", "depth": 2}
    fake = FakeReadTool(
        {
            ("repo_tree", canonical_args_key(args)): {
                "ok": True,
                "entries": [{"path": "a.py", "type": "file"}],
            },
        }
    )
    findings, _coverage = retrieve(fake, [("repo_tree", args)])
    assert [f.path for f in findings] == ["alpha/a.py"]


def test_retrieve_keeps_the_workspace_root_shape_for_a_root_tree() -> None:
    """A tree queried at "." keeps its entries workspace-relative."""
    args = {"path": ".", "depth": 2}
    fake = FakeReadTool(
        {
            ("repo_tree", canonical_args_key(args)): {
                "ok": True,
                "entries": [{"path": "alpha/a.py", "type": "file"}],
            },
        }
    )
    findings, _coverage = retrieve(fake, [("repo_tree", args)])
    assert [f.path for f in findings] == ["alpha/a.py"]


def test_retrieve_keeps_repo_rg_paths_workspace_relative() -> None:
    """``repo_rg`` already reports workspace-relative paths; do not rebase them."""
    args = {"pattern": "x", "path": "alpha", "paths": ["alpha"], "max_results": 40}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(args)): {
                "ok": True,
                "results": [{"file": "alpha/a.py", "line": 1, "snippet": "x"}],
            },
        }
    )
    findings, _coverage = retrieve(fake, [("repo_rg", args)])
    assert [f.path for f in findings] == ["alpha/a.py"]
