from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.planner import build_read_plan


def test_locate_plan_searches_each_term_with_bounded_max() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="payment gateway", mode="locate"))
    tools = [t for t, _ in plan]
    assert tools.count("repo_rg") >= 2
    rg_args = [a for t, a in plan if t == "repo_rg"][0]
    assert "--max" in rg_args


def test_boundary_plan_includes_repo_tree_for_hint_paths() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="auth module", mode="boundary", hints={"paths": ["src/auth"]}))
    assert ("repo_tree", ["src/auth", "--depth", "2"]) in plan


def test_plan_is_empty_safe_when_only_stopwords() -> None:
    # "the a is" -> no terms; plan must still be a list (possibly tree-only / empty)
    assert isinstance(build_read_plan(ScoutProbeTargetV1(query="the a is of")), list)
