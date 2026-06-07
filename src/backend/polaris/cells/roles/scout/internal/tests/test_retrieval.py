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
