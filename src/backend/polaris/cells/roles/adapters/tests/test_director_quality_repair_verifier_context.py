from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
    _resolve_cpp_quoted_include,
)


def test_quality_repair_includes_complete_go_verifier_when_helper_is_far_from_failure(tmp_path: Path) -> None:
    test_path = tmp_path / "main_test.go"
    lines = ["package main", "", 'import "testing"', ""]
    lines.extend(f"// fixture line {index}" for index in range(1, 180))
    lines.extend(
        [
            "func TestMainSmoke(t *testing.T) {",
            '    if got := readCaptured(); got == "" { t.Fatal("empty output") }',
            "}",
        ]
    )
    lines.extend(f"// spacer line {index}" for index in range(180, 360))
    lines.append('func readCaptured() string { return "" } // ROOT_CAUSE_CAPTURE_HELPER')
    test_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    failure_line = lines.index("func TestMainSmoke(t *testing.T) {") + 2
    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go verifier failure.",
        artifact_quality_errors=[f"./main_test.go:{failure_line}: smoke output missing expected content"],
        changed_files=["main_test.go"],
        repair_target_files=["main_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "CURRENT UTF-8 CONTENT OF REPAIR TARGETS" in message
    assert "ROOT_CAUSE_CAPTURE_HELPER" in message
    assert "[diagnostic excerpt" not in message


def test_quality_repair_includes_go_assertion_source_as_read_only_context(tmp_path: Path) -> None:
    """Commandless Go failures must expose test inputs without granting test writes."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text(
        "package engine\nfunc Step() {}\n",
        encoding="utf-8",
    )
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    world := worldWithGravityY(9.81) // positive Y means downward\n"
        "    got := Step(world)\n"
        '    if got.Velocity.Y > 0 { t.Fatalf("still moving downward: %v", got.Velocity.Y) }\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go behavior failure.",
        artifact_quality_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:6: still moving downward: 98.1\n"
            "FAIL\tmusicbubble/engine\t0.006s"
        ],
        changed_files=["engine/engine.go", "engine/engine_test.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "FAILING VERIFIER SOURCE CONTEXT (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert "engine/engine_test.go around line 6 (READ-ONLY)" in message
    assert "positive Y means downward" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message
    assert "Authorized tool target paths:\n- engine/engine_test.go" not in message


def test_quality_repair_includes_rust_panic_test_body_as_read_only_context(tmp_path: Path) -> None:
    """Rust assertion panics must expose the full test setup without granting test writes."""

    source_dir = tmp_path / "src"
    tests_dir = tmp_path / "tests"
    source_dir.mkdir()
    tests_dir.mkdir()
    (source_dir / "patience.rs").write_text(
        "pub struct PatienceTracker;\n",
        encoding="utf-8",
    )
    test_lines = [
        "use fantasy_restaurant::patience::PatienceTracker;",
        "",
        "#[test]",
        "fn patience_tick_with_same_value_is_a_noop() {",
        "    let mut tracker = PatienceTracker::new();",
        '    tracker.add_party("a", 5);',
        "    tracker.tick(2); // establishes last_tick before the repeated tick",
        '    let before = tracker.remaining("a");',
        "    assert_eq!(tracker.tick(2), None);",
        '    assert_eq!(tracker.remaining("a"), before);',
        "}",
    ]
    (tests_dir / "edges.rs").write_text("\n".join(test_lines) + "\n", encoding="utf-8")
    assertion_line = test_lines.index('    assert_eq!(tracker.remaining("a"), before);') + 1

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Rust behavior failure.",
        artifact_quality_errors=[
            "---- patience_tick_with_same_value_is_a_noop stdout ----\n"
            "thread 'patience_tick_with_same_value_is_a_noop' panicked at "
            f"tests/edges.rs:{assertion_line}:5:\n"
            "assertion `left == right` failed\n  left: Some(3)\n right: Some(5)"
        ],
        changed_files=["src/patience.rs", "tests/edges.rs"],
        repair_target_files=["src/patience.rs"],
        workspace_full=str(tmp_path),
    )

    assert "FAILING VERIFIER SOURCE CONTEXT (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert f"tests/edges.rs around line {assertion_line} (READ-ONLY)" in message
    assert "establishes last_tick before the repeated tick" in message
    assert "Authorized tool target paths:\n- src/patience.rs" in message
    assert "Authorized tool target paths:\n- tests/edges.rs" not in message


def test_quality_repair_includes_bounded_go_sibling_verifier_contract(tmp_path: Path) -> None:
    """One behavior fix must preserve sibling tests, not discover them by regression."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text("package engine\nfunc Step() {}\n", encoding="utf-8")
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepAppliesGravity(t *testing.T) {\n"
        "    got := Step(worldWithGravityY(9.81), 0.5)\n"
        '    if got.Velocity.Y != 4.905 { t.Fatalf("gravity velocity=%v", got.Velocity.Y) }\n'
        "}\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    got := Step(worldOnFloorWithGravityY(9.81), 0.02)\n"
        '    if got.Velocity.Y > 0 { t.Fatalf("still moving downward: %v", got.Velocity.Y) }\n'
        "}\n\n"
        "func TestStepWithRestitutionBounces(t *testing.T) {\n"
        "    got := Step(worldWithRestitution(0.5), 0.05)\n"
        '    if got.Velocity.Y <= 0 { t.Fatalf("expected upward bounce: %v", got.Velocity.Y) }\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go floor behavior failure.",
        artifact_quality_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:10: still moving downward: 98.1\n"
            "FAIL\tmusicbubble/engine\t0.006s"
        ],
        changed_files=["engine/engine.go", "engine/engine_test.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "GO SIBLING VERIFIER CONTRACT" in message
    assert "TestStepAppliesGravity" in message
    assert "TestStepClampsOnFloor" in message
    assert "TestStepWithRestitutionBounces" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message
    assert "Authorized tool target paths:\n- engine/engine_test.go" not in message


def test_quality_repair_includes_workspace_local_go_fixture_definition(tmp_path: Path) -> None:
    """Verifier calls must expose the fixture body that owns semantic conventions."""

    (tmp_path / "go.mod").write_text("module musicbubble\n\ngo 1.22\n", encoding="utf-8")
    engine = tmp_path / "engine"
    engine.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    (engine / "engine.go").write_text("package engine\nfunc Step() {}\n", encoding="utf-8")
    (models / "seed.go").write_text(
        "package models\n\n"
        "type Vector struct { X, Y float64 }\n"
        "type World struct { Gravity Vector }\n\n"
        "func SeedCMajorChord() World {\n"
        "    // Positive Y is the project's downward gravity convention.\n"
        "    return World{Gravity: Vector{X: 0, Y: 9.81}}\n"
        "}\n",
        encoding="utf-8",
    )
    (engine / "engine_test.go").write_text(
        "package engine_test\n\n"
        "import (\n"
        '    "testing"\n'
        '    "musicbubble/models"\n'
        ")\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    world := models.SeedCMajorChord()\n"
        "    _ = world\n"
        '    t.Fatal("still moving downward: 98.1")\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go floor behavior failure.",
        artifact_quality_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:11: still moving downward: 98.1\n"
            "FAIL\tmusicbubble/engine\t0.006s"
        ],
        changed_files=["engine/engine.go", "engine/engine_test.go", "models/seed.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "GO REFERENCED FIXTURE CONTRACT: models.SeedCMajorChord" in message
    assert "models/seed.go" in message
    assert "Positive Y is the project's downward gravity convention" in message
    assert "Gravity: Vector{X: 0, Y: 9.81}" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message
    assert "Authorized tool target paths:\n- models/seed.go" not in message


def test_quality_repair_includes_previous_go_failure_as_regression_guard(tmp_path: Path) -> None:
    """A repair that swaps Go test failures must see both behavior contracts."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text("package engine\nfunc Step() {}\n", encoding="utf-8")
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepAppliesGravity(t *testing.T) {\n"
        "    world := worldWithGravityY(9.81)\n"
        "    got := Step(world)\n"
        '    if got.Velocity.Y != 4.905 { t.Fatalf("velocity=%v", got.Velocity.Y) }\n'
        "}\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    world := worldOnFloorWithGravityY(9.81)\n"
        "    got := Step(world)\n"
        '    if got.Velocity.Y > 0 { t.Fatalf("still moving downward: %v", got.Velocity.Y) }\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go behavior failure.",
        artifact_quality_errors=["--- FAIL: TestStepAppliesGravity (0.00s)\n    engine_test.go:6: velocity=-4.905"],
        regression_guard_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n    engine_test.go:12: still moving downward: 98.1"
        ],
        changed_files=["engine/engine.go", "engine/engine_test.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "REGRESSION GUARDS FROM THE PREVIOUS REPAIR ROUND" in message
    assert "TestStepAppliesGravity" in message
    assert "TestStepClampsOnFloor" in message
    assert "REGRESSION GUARD VERIFIER SOURCE CONTEXT" in message
    assert "worldWithGravityY(9.81)" in message
    assert "worldOnFloorWithGravityY(9.81)" in message
    assert "do not expand authorized tool paths" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message
    assert "Authorized tool target paths:\n- engine/engine_test.go" not in message


def test_quality_repair_prompt_carries_rejected_candidate_diagnostic(tmp_path: Path) -> None:
    """A precommit rejection must alter the next same-task Director request."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text("package engine\nfunc Step() {}\n", encoding="utf-8")
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepPreservesFloor(t *testing.T) {\n"
        "    world := worldOnFloorWithGravityY(9.81)\n"
        "    got := Step(world)\n"
        '    if got.Velocity.Y != 0 { t.Fatalf("floor regression: %v", got.Velocity.Y) }\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go behavior failure.",
        artifact_quality_errors=["--- FAIL: TestStep (0.00s)\n    engine_test.go:6: wrong value"],
        candidate_rejection_errors=[
            "--- FAIL: TestStepPreservesFloor (0.00s)\n    engine_test.go:6: floor regression: -9.81"
        ],
        changed_files=["engine/engine.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "PREVIOUS CANDIDATE REJECTED BEFORE COMMIT" in message
    assert "floor regression: -9.81" in message
    assert "REJECTED CANDIDATE VERIFIER SOURCE CONTEXT" in message
    assert "worldOnFloorWithGravityY(9.81)" in message
    assert "do not repeat that rejected edit" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message


def test_quality_repair_causal_reanalysis_rejects_another_unreachable_branch_edit(tmp_path: Path) -> None:
    """Stable named tests after real edits require a causal-path rethink."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text("package engine\nfunc Step() {}\n", encoding="utf-8")
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    world := worldWithGravityY(9.81)\n"
        "    got := Step(world)\n"
        '    if got.Velocity.Y > 0 { t.Fatalf("still moving downward: %v", got.Velocity.Y) }\n'
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go behavior failure.",
        artifact_quality_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n    engine_test.go:6: still moving downward: 98.1"
        ],
        changed_files=["engine/engine.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
        causal_reanalysis_required=True,
    )

    assert "CAUSAL REANALYSIS REQUIRED AFTER VERIFIED STAGNATION" in message
    assert "edited branch may be unreachable" in message
    assert "test setup, fixture initial state, state update, and branch predicate" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message


def test_quality_repair_includes_complete_go_verifier_function(tmp_path: Path) -> None:
    """Go assertion evidence must retain setup far above the failing line."""

    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "engine.go").write_text(
        "package engine\nfunc Step() {}\n",
        encoding="utf-8",
    )
    spacer = "\n".join(f"    // simulation step {index}" for index in range(40))
    (engine / "engine_test.go").write_text(
        "package engine\n\n"
        "func TestStepClampsOnFloor(t *testing.T) {\n"
        "    world := worldWithGravityY(9.81) // VERIFIER_SETUP_POSITIVE_Y_IS_DOWN\n"
        "    const dt = 0.02\n"
        f"{spacer}\n"
        "    got := Step(world, dt)\n"
        "    // This stale comment says positive means rising.\n"
        '    if got.Velocity.Y > 0 { t.Fatalf("still moving downward: %v", got.Velocity.Y) }\n'
        "}\n\n"
        "func TestOther(t *testing.T) { t.Skip() }\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go behavior failure.",
        artifact_quality_errors=[
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:47: still moving downward: 98.1\n"
            "FAIL\tmusicbubble/engine\t0.006s"
        ],
        changed_files=["engine/engine.go", "engine/engine_test.go"],
        repair_target_files=["engine/engine.go"],
        workspace_full=str(tmp_path),
    )

    assert "VERIFIER_SETUP_POSITIVE_Y_IS_DOWN" in message
    assert "simulation step 0" in message
    assert "simulation step 39" in message
    assert "func TestOther" not in message
    assert "Executable setup, calls, and assertions are authoritative" in message
    assert "passes only when `<condition>` becomes false" in message
    assert "`x -= 0`" in message
    assert "semantic no-ops" in message
    assert "Authorized tool target paths:\n- engine/engine.go" in message
    assert "Authorized tool target paths:\n- engine/engine_test.go" not in message


def test_quality_repair_includes_read_only_referenced_rust_definitions(tmp_path: Path) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "treasure_rules.rs").write_text(
        "fn evaluate_reef(reef: &Reef) {\n    match reef.hazard() {\n        ReefHazard::Shoal => {}\n    }\n}\n",
        encoding="utf-8",
    )
    (models / "reef.rs").write_text(
        "pub enum ReefHazard {\n    Calm,\n    Shallow,\n    Treacherous,\n}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the rustc failure.",
        artifact_quality_errors=[
            "error[E0599]: no variant named `Shoal` found for enum `reef::ReefHazard`\n"
            "   --> src/engine/treasure_rules.rs:3:21\n"
            "    |\n"
            "   ::: src/models/reef.rs:1:1\n"
            "    |\n"
            "1  | pub enum ReefHazard {\n"
        ],
        changed_files=["src/engine/treasure_rules.rs"],
        repair_target_files=["src/engine/treasure_rules.rs"],
        workspace_full=str(tmp_path),
    )

    assert "REFERENCED TYPE DEFINITIONS (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert "src/models/reef.rs" in message
    assert "Shallow" in message
    assert "Never invent members" in message


def test_quality_repair_includes_named_type_impl_when_rustc_omits_definition_path(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "treasure_rules.rs").write_text(
        "fn check(budget: &Budget) { let _ = budget.remaining(); }\n",
        encoding="utf-8",
    )
    (models / "budget.rs").write_text(
        "pub struct Budget {\n    total: f64,\n}\n\nimpl Budget {\n    pub fn spendable(&self) -> f64 { self.total }\n    pub fn classify(&self, cost: f64) -> i32 { 0 }\n}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the rustc failure.",
        artifact_quality_errors=[
            "error[E0599]: no method named `remaining` found for reference `&Budget` in the current scope\n"
            "   --> src/engine/treasure_rules.rs:1:45\n"
            "    |\n"
            "1  | fn check(budget: &Budget) { let _ = budget.remaining(); }\n"
            "    |                                             ^^^^^^^^^ method not found in `&Budget`\n"
        ],
        changed_files=["src/engine/treasure_rules.rs"],
        repair_target_files=["src/engine/treasure_rules.rs"],
        workspace_full=str(tmp_path),
    )

    assert "src/models/budget.rs" in message
    assert "spendable" in message
    assert "classify" in message
    assert "READ-ONLY DEFINITION" in message


def test_quality_repair_includes_typescript_callee_and_result_unwrap(
    tmp_path: Path,
) -> None:
    """tsc TS2554/TS2322 must project existing restock/isOk, not relax tsconfig.

    Live L2-17 remint-4: DomainResult<Inventory> assigned into Inventory and
    restock(Item) vs ItemId. Bare TS2322 triggered STRICT-NULL advice and the
    definition block never showed the exported restock signature.
    """

    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "simulation.ts").write_text(
        "import { restock, isOk } from '../models/inventory.js';\n"
        "export function seed() {\n"
        "  const stocked = restock(inventory, { id: itemId, name: 'x' }, 6, 0);\n"
        "  return { inventories: { [stallId]: stocked } };\n"
        "}\n",
        encoding="utf-8",
    )
    (models / "inventory.ts").write_text(
        "export function restock(inventory: Inventory, itemId: ItemId, quantity: number, unitCost: Coin) {\n"
        "  return ok(inventory);\n"
        "}\n"
        "export function isOk<T>(result: DomainResult<T>): result is DomainOk<T> {\n"
        "  return result.ok === true;\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the TypeScript quality failure.",
        artifact_quality_errors=[
            "src/engine/simulation.ts(3,19): error TS2345: Argument of type '{ id: Brand<string, \"ItemId\">; name: string; }' is not assignable to parameter of type 'ItemId'.",
            "src/engine/simulation.ts(4,5): error TS2322: Type '{ [x: string]: DomainResult<Inventory>; }' is not assignable to type 'Readonly<Record<string, Inventory>>'.",
        ],
        changed_files=["src/engine/simulation.ts"],
        repair_target_files=["src/engine/simulation.ts"],
        workspace_full=str(tmp_path),
    )

    assert "REFERENCED TYPE DEFINITIONS (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert "src/models/inventory.ts" in message
    assert "export function restock" in message
    assert "itemId: ItemId" in message
    assert "isOk" in message
    assert "unwrap" in message.lower()
    assert "STRICT-NULL RELAXATION" not in message


def test_quality_repair_includes_javascript_tap_callee_and_call_site(
    tmp_path: Path,
) -> None:
    """Node TAP TypeError must project grantWish(wish, meteorId) and the 1-arg call.

    Live L2-18 remint-5: ten QA rounds swapped tests/product.test.js and
    src/wish.js. TAP location pointed at the test() header (line 206) while
    grantWish(closeWish(openWish(w))) sat at line 211. The impl signature
    grantWish(wish, meteorId) never entered the test-owner prompt, and the
    TAP call never entered the impl-owner prompt, so both sides stagnated
    on meteorId must be a non-empty string.
    """

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "wish.js").write_text(
        "function closeWish(wish) {\n"
        "  return { ...wish, status: 'closed', meteorId: null };\n"
        "}\n"
        "function grantWish(wish, meteorId) {\n"
        "  if (typeof meteorId !== 'string' || meteorId.length === 0) {\n"
        "    throw new TypeError('meteorId must be a non-empty string');\n"
        "  }\n"
        "  return { ...wish, status: 'granted', meteorId };\n"
        "}\n"
        "export { closeWish, grantWish };\n",
        encoding="utf-8",
    )
    (tests / "product.test.js").write_text(
        "import { closeWish, grantWish } from '../src/wish.js';\n"
        "describe('wish lifecycle', () => {\n"
        "  test('open → close → grant lifecycle is observable', () => {\n"
        "    const w = { id: 'w-1', meteorId: 'm-1', status: 'open' };\n"
        "    const granted = grantWish(closeWish(openWish(w)));\n"
        "    assert.equal(granted.status, 'granted');\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    tap_error = (
        "not ok 2 - open → close → grant lifecycle is observable\n"
        "      ---\n"
        f"      location: '{tests / 'product.test.js'}:3:3'\n"
        "      failureType: 'testCodeFailure'\n"
        "      error: 'meteorId must be a non-empty string'\n"
        "      code: 'ERR_TEST_FAILURE'\n"
    )

    test_owner = _build_materialization_quality_repair_message(
        original_message="Repair the Node TAP quality failure.",
        artifact_quality_errors=[tap_error],
        changed_files=["tests/product.test.js"],
        repair_target_files=["tests/product.test.js"],
        workspace_full=str(tmp_path),
    )
    assert "REFERENCED TYPE DEFINITIONS (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in test_owner
    assert "src/wish.js" in test_owner
    assert "function grantWish(wish, meteorId)" in test_owner
    assert "READ-ONLY DEFINITION" in test_owner
    assert "change the call-site" in test_owner.lower() or "call-site" in test_owner.lower()
    assert "prior helper" in test_owner.lower()
    assert "accepted state" in test_owner.lower()

    impl_owner = _build_materialization_quality_repair_message(
        original_message="Repair the Node TAP quality failure.",
        artifact_quality_errors=[tap_error],
        changed_files=["src/wish.js"],
        repair_target_files=["src/wish.js"],
        workspace_full=str(tmp_path),
    )
    assert "FAILING VERIFIER SOURCE CONTEXT (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in impl_owner
    assert "tests/product.test.js" in impl_owner
    assert "grantWish(closeWish(openWish(w)))" in impl_owner
    assert "function grantWish(wish, meteorId)" not in impl_owner.split("CURRENT UTF-8 CONTENT OF REPAIR TARGETS")[0]


def test_quality_repair_includes_go_callee_definition_for_test_owner(tmp_path: Path) -> None:
    """A Go test repair must see the implementation signature it is calling."""

    physics = tmp_path / "internal" / "physics"
    physics.mkdir(parents=True)
    (physics / "step.go").write_text(
        "package physics\n\ntype World struct{}\n\nfunc ApplyGravity(w *World) float64 {\n\treturn 9.8\n}\n",
        encoding="utf-8",
    )
    (physics / "step_test.go").write_text(
        "package physics\n\n"
        "func TestApplyGravity(t *testing.T) {\n"
        "\tw := &World{}\n"
        "\tif err := ApplyGravity(w); err != nil { t.Fatal(err) }\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go compiler failure.",
        artifact_quality_errors=[
            "internal/physics/step_test.go:5:12: assignment mismatch: 1 variable but ApplyGravity returns 1 value"
        ],
        changed_files=["internal/physics/step_test.go"],
        repair_target_files=["internal/physics/step_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "GO SAME-PACKAGE DEFINITIONS" in message
    assert "internal/physics/step.go" in message
    assert "func ApplyGravity(w *World) float64" in message
    assert "READ-ONLY" in message


def test_quality_repair_includes_go_package_inventory_for_unresolved_test_symbol(tmp_path: Path) -> None:
    """Undefined test helpers must see case variants and real package-owned types."""

    sandbox = tmp_path / "internal" / "sandbox"
    sandbox.mkdir(parents=True)
    (sandbox / "sandbox.go").write_text(
        "package sandbox\n\n"
        "type Scenario struct {\n"
        "\tBubbles []physics.Bubble\n"
        "}\n\n"
        "func run(args []string) error { return nil }\n",
        encoding="utf-8",
    )
    (sandbox / "sandbox_test.go").write_text(
        "package sandbox\n\nfunc TestScenario(t *testing.T) {\n\t_ = Run(nil)\n\t_ = append([]bubbleType(nil))\n}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair undefined Go test symbols.",
        artifact_quality_errors=[
            "internal/sandbox/sandbox_test.go:4:6: undefined: Run",
            "internal/sandbox/sandbox_test.go:5:15: undefined: bubbleType",
        ],
        changed_files=["internal/sandbox/sandbox_test.go"],
        repair_target_files=["internal/sandbox/sandbox_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "GO SAME-PACKAGE DEFINITIONS" in message
    assert "func run(args []string) error" in message
    assert "type Scenario struct" in message
    assert "Bubbles []physics.Bubble" in message


def test_quality_repair_javascript_reference_error_restores_const(tmp_path: Path) -> None:
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "wish.js").write_text(
        "function validateWish(wish) {\n  text = clampString(wish.text, 'wish.text', 500);\n}\n",
        encoding="utf-8",
    )
    (tests / "product.test.js").write_text("test('createWish', () => { makeWish(); });\n", encoding="utf-8")
    message = _build_materialization_quality_repair_message(
        original_message="Repair the Node TAP quality failure.",
        artifact_quality_errors=[
            "not ok 1 - createWish returns an OPEN wish\n"
            f"      location: '{tests / 'product.test.js'}:1:3'\n"
            "      error: 'text is not defined'\n"
            "      name: 'ReferenceError'\n"
        ],
        changed_files=["src/wish.js"],
        repair_target_files=["src/wish.js"],
        workspace_full=str(tmp_path),
    )
    assert "REFERENCEERROR" in message.upper()
    assert "const/let" in message.lower() or "dropped const" in message.lower()


def test_quality_repair_javascript_syntax_error_restores_describe_opener(tmp_path: Path) -> None:
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "queue.js").write_text("export function enqueue() {}\n", encoding="utf-8")
    (tests / "product.test.js").write_text("test('x', () => {});\n});\n", encoding="utf-8")
    message = _build_materialization_quality_repair_message(
        original_message="Repair the Node TAP quality failure.",
        artifact_quality_errors=[
            "Artifact quality scan failed: syntax error in tests/product.test.js: "
            "product.test.js:152\n});\n^\n\nSyntaxError: Unexpected token '}'\n"
        ],
        changed_files=["tests/product.test.js"],
        repair_target_files=["tests/product.test.js"],
        workspace_full=str(tmp_path),
    )
    assert "SYNTAXERROR" in message.upper()
    assert "describe(" in message
    assert "opener" in message.lower()


def test_quality_repair_includes_named_cpp_class_when_gxx_omits_header_path(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "generator.cpp").write_text(
        "bool can_step(const patrol_chess::models::Robot& robot) { return robot.energy(); }\n",
        encoding="utf-8",
    )
    (models / "robot.hpp").write_text(
        "namespace patrol_chess::models {\n"
        "class Robot {\n"
        "public:\n"
        "    bool begin_move();\n"
        "    Position position() const;\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )
    (models / "energy.hpp").write_text(
        "namespace patrol_chess::models {\n"
        "class Energy {\n"
        "public:\n"
        "    bool can_spend(unsigned delta) const;\n"
        "    unsigned current() const;\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the g++ failure.",
        artifact_quality_errors=[
            "src/engine/generator.cpp:49:42: error: ‘const class patrol_chess::models::Robot’ "
            "has no member named ‘energy’\n"
        ],
        changed_files=["src/engine/generator.cpp"],
        repair_target_files=["src/engine/generator.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "src/models/robot.hpp" in message
    assert "src/models/energy.hpp" in message
    assert "begin_move" in message
    assert "can_spend" in message
    assert "READ-ONLY DEFINITION" in message
    assert "Never invent members" in message
    assert "qualify the use-site as ::NS::models" in message
    assert "An unclosed A swallows later includes into A::std" in message


def test_quality_repair_projects_existing_cpp_enum_and_included_header_api(tmp_path: Path) -> None:
    """g++ 'X is not a member of NS' must show existing labels, not invent to_string.

    Live L2-20 reminted src/main.cpp after isfinite / string-to-EntityKind.
    The model invented wind::to_string, severity_to_string, and
    ResultStatus::Partial while result.hpp already had result_status_label
    and ResultStatus::{Ok,Warn,Fail,Empty,InvalidInput}.
    """

    src = tmp_path / "src"
    models = src / "models"
    models.mkdir(parents=True)
    (src / "main.cpp").write_text(
        '#include "models/result.hpp"\n'
        '#include "models/entity.hpp"\n'
        "int main() { wind::ResultStatus status = wind::ResultStatus::Partial; }\n",
        encoding="utf-8",
    )
    (models / "result.hpp").write_text(
        "#pragma once\n"
        "#include <string_view>\n"
        "namespace wind {\n"
        "enum class ResultStatus : std::uint8_t {\n"
        "    Ok = 0,\n"
        "    Warn = 1,\n"
        "    Fail = 2,\n"
        "    Empty = 3,\n"
        "    InvalidInput = 4,\n"
        "};\n"
        "[[nodiscard]] std::string_view result_status_label(ResultStatus s) noexcept;\n"
        "[[nodiscard]] bool try_parse_result_status(std::string_view text, ResultStatus& out) noexcept;\n"
        "}\n",
        encoding="utf-8",
    )
    (models / "entity.hpp").write_text(
        "#pragma once\n"
        "#include <string_view>\n"
        "namespace wind {\n"
        "enum class EntityKind : std::uint8_t { Unknown = 0, Sensor = 1 };\n"
        "[[nodiscard]] std::string_view kind_label(EntityKind kind) noexcept;\n"
        "[[nodiscard]] bool try_parse_kind(std::string_view text, EntityKind& out) noexcept;\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the g++ leftover failure.",
        artifact_quality_errors=[
            "src/main.cpp:3:48: error: ‘Partial’ is not a member of ‘wind::ResultStatus’\n"
            "src/main.cpp:438:27: error: cannot convert ‘const std::__cxx11::basic_string<char>’ "
            "to ‘wind::EntityKind’ in assignment\n"
            "src/main.cpp:526:41: error: ‘to_string’ is not a member of ‘wind’\n"
        ],
        changed_files=["src/main.cpp"],
        repair_target_files=["src/main.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "REFERENCED TYPE DEFINITIONS (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert "src/models/result.hpp" in message
    assert "result_status_label" in message
    assert "InvalidInput" in message
    assert "EXISTING C++ PUBLIC API FROM INCLUDED HEADERS" in message
    assert "try_parse_kind" in message
    assert "Never invent NS::to_string" in message
    assert "READ-ONLY EXISTING API" in message
    assert "EXISTING ENUMERATORS" in message
    assert "ResultStatus = Ok, Warn, Fail, Empty, InvalidInput" in message
    assert "Do not invent Partial" in message


def test_quality_repair_projects_complete_cpp_class_contract_from_read_only_header(tmp_path: Path) -> None:
    """Repair context must preserve methods inside class bodies, including inline definitions.

    Exact-run regression: L3-24 r11 only projected ``class Diary {`` from
    ``diary.hpp``. Director then redefined the existing inline ``size()`` and
    invented undeclared ``empty``/``clear`` methods while editing diary.cpp.
    """

    include = tmp_path / "include" / "invisible_diary"
    src = tmp_path / "src" / "invisible_diary"
    include.mkdir(parents=True)
    src.mkdir(parents=True)
    (src / "diary.cpp").write_text(
        '#include "invisible_diary/diary.hpp"\nnamespace InvisibleDiary {}\n',
        encoding="utf-8",
    )
    (include / "diary.hpp").write_text(
        "#pragma once\n"
        "#include <cstddef>\n"
        "#include <vector>\n"
        "namespace InvisibleDiary {\n"
        "class Diary {\n"
        "public:\n"
        "    std::size_t size() const noexcept { return entries_.size(); }\n"
        "    const int& entry(std::size_t index) const;\n"
        "private:\n"
        "    std::vector<int> entries_;\n"
        "};\n"
        "Diary make_diary(std::size_t reserve_count) noexcept;\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair implementation depth without changing public contracts.",
        artifact_quality_errors=[
            "implementation depth metrics: prod_lines=499; failures: production_source_lines=499 < 650"
        ],
        changed_files=["src/invisible_diary/diary.cpp"],
        repair_target_files=["src/invisible_diary/diary.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "std::size_t size() const noexcept { return entries_.size(); }" in message
    assert "const int& entry(std::size_t index) const;" in message
    assert "std::vector<int> entries_;" in message
    assert "Diary make_diary(std::size_t reserve_count) noexcept;" in message


def test_quality_repair_projects_parent_relative_cpp_header_contract(tmp_path: Path) -> None:
    """Parent-relative includes must retain their in-workspace read-only API.

    Exact-run regression: L3-24 r50 repaired ``src/ink_ledger.cpp``, which
    includes ``../include/core_engine/ink_ledger.hpp``. The repair request
    omitted that header while requiring public-contract preservation, so two
    attempts invented incompatible method signatures and were rolled back.
    """

    include = tmp_path / "include" / "core_engine"
    src = tmp_path / "src"
    include.mkdir(parents=True)
    src.mkdir(parents=True)
    (src / "ink_ledger.cpp").write_text(
        '#include "../include/core_engine/ink_ledger.hpp"\nnamespace invisible_diary {}\n',
        encoding="utf-8",
    )
    (include / "ink_ledger.hpp").write_text(
        "#pragma once\n"
        "#include <string>\n"
        "namespace invisible_diary {\n"
        "class InkLedger {\n"
        "public:\n"
        "    std::string render_visible_block(int moon_day) const;\n"
        "    std::string debug_dump() const;\n"
        "    void clear();\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair behavior without changing public contracts.",
        artifact_quality_errors=["test_revealed_block_exposes_plaintext: expected plaintext missing"],
        changed_files=["src/ink_ledger.cpp"],
        repair_target_files=["src/ink_ledger.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "include/core_engine/ink_ledger.hpp" in message
    assert "std::string render_visible_block(int moon_day) const;" in message
    assert "std::string debug_dump() const;" in message
    assert "void clear();" in message


def test_cpp_parent_relative_header_resolution_rejects_workspace_escape(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    importer = src / "engine.cpp"
    importer.write_text('#include "../../outside.hpp"\n', encoding="utf-8")
    outside = tmp_path.parent / "outside.hpp"
    outside.write_text("void must_not_leak();\n", encoding="utf-8")

    assert _resolve_cpp_quoted_include(tmp_path, importer, "../../outside.hpp") is None


def test_quality_repair_prompt_names_leftover_cmake_include_roots(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
        _build_materialization_quality_repair_message,
    )

    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(wind LANGUAGES CXX)\nadd_executable(wind src/main.cpp)\n",
        encoding="utf-8",
    )
    message = _build_materialization_quality_repair_message(
        original_message="Repair leftover cmake include roots.",
        artifact_quality_errors=[
            "CMakeLists.txt:1:1: error: official leftover cmake requires "
            "target_include_directories covering CE-declared include roots (src)\n"
        ],
        changed_files=[],
        repair_target_files=["CMakeLists.txt"],
        workspace_full=str(tmp_path),
    )
    assert "LEFTOVER CMAKE INCLUDE ROOTS" in message
    assert "target_include_directories(<existing_executable> PRIVATE <those roots>)" in message
    assert "Do not invent src/models" in message


def test_quality_repair_prompt_remaps_linker_undefined_ref_to_defined_sibling(tmp_path: Path) -> None:
    """cmake --build linker residual must remint the use-site to a defined .cpp name.

    Live L2-20: leftover remint added ``entity_kind_label`` as a declaration-only
    alias in entity.hpp. g++ -fsyntax-only passed; cmake --build failed with
    ``undefined reference to wind::entity_kind_label`` from generator.cpp.o.
    entity.cpp only defines ``kind_label``. Polar is must not treat the header
    alias as public API or tell leftover remint to implement it.
    """

    from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
        _build_materialization_quality_repair_message,
    )

    src = tmp_path / "src"
    models = src / "models"
    engine = src / "engine"
    models.mkdir(parents=True)
    engine.mkdir(parents=True)
    (models / "entity.hpp").write_text(
        "#pragma once\n"
        "#include <string_view>\n"
        "namespace wind {\n"
        "enum class EntityKind : std::uint8_t { Unknown = 0, Sensor = 1 };\n"
        "[[nodiscard]] std::string_view kind_label(EntityKind kind) noexcept;\n"
        "[[nodiscard]] std::string_view entity_kind_label(EntityKind kind) noexcept;\n"
        "[[nodiscard]] bool try_parse_kind(std::string_view text, EntityKind& out) noexcept;\n"
        "}\n",
        encoding="utf-8",
    )
    (models / "entity.cpp").write_text(
        '#include "models/entity.hpp"\n'
        "namespace wind {\n"
        "std::string_view kind_label(EntityKind kind) noexcept {\n"
        '    return kind == EntityKind::Sensor ? "sensor" : "unknown";\n'
        "}\n"
        "bool try_parse_kind(std::string_view text, EntityKind& out) noexcept {\n"
        '    if (text == "sensor") { out = EntityKind::Sensor; return true; }\n'
        "    return false;\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )
    (engine / "generator.cpp").write_text(
        '#include "models/entity.hpp"\n'
        "namespace wind {\n"
        "bool supported(Entity e) { return !entity_kind_label(e.kind).empty(); }\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair leftover cmake --build linker failure.",
        artifact_quality_errors=[
            "CMakeFiles/wind-translator.dir/src/engine/generator.cpp.o: in function "
            "`wind::ValidateGenerator::process`:\n"
            "generator.cpp:(.text+0x1205): undefined reference to "
            "`wind::entity_kind_label(wind::EntityKind)'\n"
        ],
        changed_files=["src/engine/generator.cpp"],
        repair_target_files=["src/engine/generator.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "EXISTING DEFINED C++ FUNCTIONS" in message
    assert "kind_label" in message
    assert "try_parse_kind" in message
    assert "DECLARED BUT NOT DEFINED" in message
    assert "entity_kind_label" in message
    assert "LEFTOVER LINKER UNDEFINED REFERENCE" in message
    assert "Never add a declaration-only alias" in message
    assert "Remint the use-site" in message or "remap the use-site" in message.lower()
    assert "LEFTOVER CMAKE MUST NOT GENERATE TRANSLATION UNITS" in message
    assert "file(WRITE)" in message


def test_quality_repair_prompt_recognizes_repair_target_as_header_implementation(tmp_path: Path) -> None:
    """An included header may be implemented in ``src/``, not beside ``include/``.

    Live L3-24 r39 repaired ``src/cipher.cpp`` while its public header lived at
    ``include/invisible_diary/cipher.hpp``.  The prompt scanned only a sibling
    ``include/invisible_diary/cipher.cpp``, falsely labelled real definitions as
    declaration-only, and told Director not to call the API it was repairing.
    """

    from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
        _build_materialization_quality_repair_message,
    )

    include = tmp_path / "include" / "invisible_diary"
    src = tmp_path / "src"
    include.mkdir(parents=True)
    src.mkdir(parents=True)
    (include / "cipher.hpp").write_text(
        "#pragma once\nnamespace invisible_diary {\nint encrypt(int value);\nint decrypt(int value);\n}\n",
        encoding="utf-8",
    )
    (src / "cipher.cpp").write_text(
        '#include "invisible_diary/cipher.hpp"\n'
        "namespace invisible_diary {\n"
        "int encrypt(int value) { return value + 1; }\n"
        "int decrypt(int value) { return value - 1; }\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair cipher behavior.",
        artifact_quality_errors=["tests/cpp_golden.cpp:60 cipher round-trip failed"],
        changed_files=["src/cipher.cpp"],
        repair_target_files=["src/cipher.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "EXISTING DEFINED C++ FUNCTIONS" in message
    defined_block = message.split("EXISTING DEFINED C++ FUNCTIONS", 1)[1]
    assert "- encrypt" in defined_block
    assert "- decrypt" in defined_block
    declaration_block = message.split("DECLARED BUT NOT DEFINED", 1)[1] if "DECLARED BUT NOT DEFINED" in message else ""
    assert "- encrypt" not in declaration_block
    assert "- decrypt" not in declaration_block


def test_quality_repair_prompt_recognizes_qualified_member_function_definitions(tmp_path: Path) -> None:
    """A concrete ``Type::method`` body must not be called declaration-only.

    Exact L3-24 r67 final-request evidence injected complete
    ``Moonlight::days_since_1900`` and ``Moonlight::phase_from_date`` bodies,
    then contradicted itself by placing both names under ``DECLARED BUT NOT
    DEFINED``.  That false linker guidance steered a same-task semantic repair
    while the target was correctly scoped and physically editable.
    """

    from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
        _cpp_defined_and_declaration_only_names,
    )

    src = tmp_path / "src" / "inkwell"
    src.mkdir(parents=True)
    (src / "moonlight.hpp").write_text(
        "#pragma once\n"
        "#include <string>\n"
        "namespace inkwell {\n"
        "enum class MoonPhase { NewMoon, FullMoon, Unknown };\n"
        "class Moonlight {\n"
        "public:\n"
        "    static long days_since_1900(const std::string& iso);\n"
        "    static MoonPhase phase_from_date(const std::string& iso);\n"
        "    static std::string normalize_label(std::string label);\n"
        "    static long declaration_only(const std::string& iso);\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "moonlight.cpp").write_text(
        '#include "moonlight.hpp"\n'
        "#include <algorithm>\n"
        "#include <cctype>\n"
        "namespace inkwell {\n"
        "long Moonlight::days_since_1900(const std::string& iso) { return iso.empty() ? -1 : 0; }\n"
        "MoonPhase Moonlight::phase_from_date(const std::string& iso) {\n"
        "    return iso.empty() ? MoonPhase::Unknown : MoonPhase::FullMoon;\n"
        "}\n"
        "std::string Moonlight::normalize_label(std::string label)\n"
        "{\n"
        "    std::transform(label.begin(), label.end(), label.begin(),\n"
        "                   [](unsigned char c) { return std::tolower(c); });\n"
        "    return label;\n"
        "}\n"
        "}\n",
        encoding="utf-8",
    )

    defined_names, declaration_only = _cpp_defined_and_declaration_only_names(
        workspace=tmp_path,
        headers=[src / "moonlight.hpp"],
        repair_target_files=["src/inkwell/moonlight.cpp"],
    )

    assert "days_since_1900" in defined_names
    assert "phase_from_date" in defined_names
    assert "normalize_label" in defined_names
    assert "transform" not in defined_names
    assert "days_since_1900" not in declaration_only
    assert "phase_from_date" not in declaration_only
    assert "declaration_only" in declaration_only
