from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
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
