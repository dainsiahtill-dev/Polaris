from polaris.cells.roles.scout.internal.ports import FakeDistiller, FakeReadTool


def test_fake_read_tool_returns_scripted_result() -> None:
    fake = FakeReadTool({("repo_rg", ("pay",)): {"ok": True, "hits": [{"file": "a.py", "line": 3, "text": "pay"}]}})
    out = fake.run("repo_rg", ["pay"])
    assert out["hits"][0]["file"] == "a.py"
    assert fake.calls == [("repo_rg", ["pay"])]


def test_fake_read_tool_defaults_to_empty_ok() -> None:
    assert FakeReadTool({}).run("repo_tree", ["."]) == {"ok": True, "hits": [], "stdout": ""}


async def test_fake_distiller_echoes() -> None:
    out = await FakeDistiller("SUMMARY").distill(query="q", findings=[], token_budget=10)
    assert out == "SUMMARY"
