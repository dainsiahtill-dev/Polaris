from polaris.cells.roles.scout.internal.ports import FakeReadTool
from polaris.cells.roles.scout.internal.retrieval import retrieve


def test_retrieve_parses_rg_hits_into_findings() -> None:
    fake = FakeReadTool(
        {
            ("repo_rg", ("pay", "--max", "40")): {
                "ok": True,
                "hits": [{"file": "a.py", "line": 3, "text": "def pay():"}],
            },
        }
    )
    plan = [("repo_rg", ["pay", "--max", "40"])]
    findings, coverage = retrieve(fake, plan)
    assert findings[0].path == "a.py"
    assert findings[0].line == 3
    assert coverage["tools_used"] == ["repo_rg"]
    assert coverage["truncated"] is False


def test_retrieve_marks_truncation_and_survives_tool_error() -> None:
    fake = FakeReadTool(
        {
            ("repo_rg", ("x",)): {"ok": True, "hits": [{"file": "b.py", "line": 1, "text": "x"}], "truncated": True},
            ("repo_tree", ("bad",)): {"ok": False, "error": "boom"},
        }
    )
    findings, coverage = retrieve(fake, [("repo_rg", ["x"]), ("repo_tree", ["bad"])])
    assert coverage["truncated"] is True
    assert any("repo_tree" in e for e in coverage["errors"])
    assert len(findings) == 1
