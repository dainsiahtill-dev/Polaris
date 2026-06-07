from polaris.cells.roles.scout.internal.planner import build_read_plan
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1


def test_locate_plan_searches_each_term_with_bounded_max() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="payment gateway", mode="locate"))
    tools = [t for t, _ in plan]
    assert tools.count("repo_rg") >= 2
    rg_args = next(a for t, a in plan if t == "repo_rg")
    assert "pattern" in rg_args
    assert rg_args["max_results"] == 40


def test_locate_plan_includes_tree_and_symbols_index() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="payment", mode="locate"))
    tools = [t for t, _ in plan]
    assert "repo_tree" in tools
    assert "repo_symbols_index" in tools
    symbols_args = next(a for t, a in plan if t == "repo_symbols_index")
    assert symbols_args["paths"] == ["."]


def test_boundary_plan_includes_repo_tree_for_hint_paths() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="auth module", mode="boundary", hints={"paths": ["src/auth"]}))
    assert ("repo_tree", {"path": "src/auth", "depth": 2}) in plan
    # boundary mode is structure-only: no text search
    assert all(t != "repo_rg" for t, _ in plan)


def test_locate_plan_passes_hint_path_and_glob_to_rg() -> None:
    plan = build_read_plan(
        ScoutProbeTargetV1(query="login", mode="locate", hints={"paths": ["src/auth"], "globs": ["*.py"]})
    )
    rg_args = next(a for t, a in plan if t == "repo_rg")
    assert rg_args["path"] == "src/auth"
    assert rg_args["glob"] == "*.py"


def test_plan_is_empty_safe_when_only_stopwords() -> None:
    # "the a is of" -> no terms; plan must still be a list (tree + symbols, no rg)
    plan = build_read_plan(ScoutProbeTargetV1(query="the a is of"))
    assert isinstance(plan, list)
    assert all(t != "repo_rg" for t, _ in plan)
