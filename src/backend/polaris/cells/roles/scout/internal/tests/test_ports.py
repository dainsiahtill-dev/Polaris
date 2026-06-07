from polaris.cells.roles.scout.internal.ports import FakeDistiller, FakeReadTool, canonical_args_key


def test_fake_read_tool_returns_scripted_result() -> None:
    args = {"pattern": "pay", "max_results": 40}
    fake = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(args)): {
                "ok": True,
                "results": [{"file": "a.py", "line": 3, "snippet": "pay"}],
            }
        }
    )
    out = fake.run("repo_rg", {"max_results": 40, "pattern": "pay"})  # order-independent key
    assert out["results"][0]["file"] == "a.py"
    assert fake.calls == [("repo_rg", {"max_results": 40, "pattern": "pay"})]


def test_fake_read_tool_defaults_to_empty_ok() -> None:
    assert FakeReadTool({}).run("repo_tree", {"path": "."}) == {"ok": True, "hits": [], "stdout": ""}


def test_canonical_args_key_is_order_independent() -> None:
    assert canonical_args_key({"a": 1, "b": 2}) == canonical_args_key({"b": 2, "a": 1})


async def test_fake_distiller_echoes() -> None:
    out = await FakeDistiller("SUMMARY").distill(query="q", findings=[], token_budget=10)
    assert out == "SUMMARY"
