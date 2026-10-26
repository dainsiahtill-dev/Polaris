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


# ── host-authority planning (read_roots) ──


def test_read_roots_plan_searches_every_authorized_root() -> None:
    """Every host-authorized root must be searched, not only the first one."""
    plan = build_read_plan(
        ScoutProbeTargetV1(query="payment", mode="locate"),
        read_roots=("alpha", "beta"),
    )
    tree_roots = [a["path"] for t, a in plan if t == "repo_tree"]
    assert tree_roots == ["alpha", "beta"]

    rg_paths = [a["path"] for t, a in plan if t == "repo_rg"]
    assert "alpha" in rg_paths
    assert "beta" in rg_paths

    symbols = next(a for t, a in plan if t == "repo_symbols_index")
    assert symbols["paths"] == ["alpha", "beta"]


def test_read_roots_plan_names_both_path_operands_for_the_scope_gate() -> None:
    """``repo_rg`` must name the root in BOTH declared path operands.

    The canonical read-scope gate derives its operand from the ToolSpec's
    PATH_FIELDS (``paths`` AND ``path``). A step that named only one of them
    would be refused by the gate.
    """
    plan = build_read_plan(
        ScoutProbeTargetV1(query="payment", mode="locate"),
        read_roots=("alpha",),
    )
    for tool, args in plan:
        if tool != "repo_rg":
            continue
        assert args["path"] == "alpha"
        assert args["paths"] == ["alpha"]


def test_model_hints_only_narrow_the_authorized_roots() -> None:
    """A hint may narrow the root set; it must never add a root."""
    plan = build_read_plan(
        ScoutProbeTargetV1(query="payment", mode="locate", hints={"paths": ["alpha"]}),
        read_roots=("alpha", "beta"),
    )
    tree_roots = [a["path"] for t, a in plan if t == "repo_tree"]
    assert tree_roots == ["alpha"], f"a hint must narrow, not widen: {tree_roots!r}"
    assert "beta" not in tree_roots


def test_a_hint_outside_the_authorized_roots_adds_no_root() -> None:
    """A hint naming an unauthorized path must not smuggle it into the plan."""
    plan = build_read_plan(
        ScoutProbeTargetV1(query="payment", mode="locate", hints={"paths": ["private"]}),
        read_roots=("alpha",),
    )
    tree_roots = [a["path"] for t, a in plan if t == "repo_tree"]
    assert "private" not in tree_roots
    assert tree_roots == ["alpha"]


def test_no_read_roots_keeps_the_legacy_workspace_root_plan() -> None:
    """Without a committed scope the planner keeps its historical behaviour."""
    plan = build_read_plan(ScoutProbeTargetV1(query="payment", mode="locate"))
    tree_roots = [a["path"] for t, a in plan if t == "repo_tree"]
    assert tree_roots == ["."]


def test_empty_read_roots_produce_no_searchable_root() -> None:
    """An explicitly empty scope must not fall back to the workspace root."""
    plan = build_read_plan(
        ScoutProbeTargetV1(query="payment", mode="locate"),
        read_roots=(),
    )
    tree_roots = [a["path"] for t, a in plan if t == "repo_tree"]
    assert tree_roots == [], f"an empty scope must not search the workspace: {tree_roots!r}"
    rg_paths = [a.get("path") for t, a in plan if t == "repo_rg"]
    assert all(path is None for path in rg_paths)
