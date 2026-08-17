"""Official TypeScript quality leftover must parse tsc and npm-test stacks.

Live L2-17 remint-3: eight quality rounds stayed on TASK-2
``src/engine/simulation.ts`` while ``src/models/reputation.ts`` still had
TS2322 and ``npm test`` pointed at ``src/verify.ts``. leftover only accepted
``### FAILING_TUS`` C++/Java suffixes, so owner rotate never fired.
Do not hand-edit generated projects.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    leftover_rotate_allows_quality_extra_round,
    leftover_targets_should_force_owner_rotate,
    workspace_quality_unclaimed_failing_tu_targets,
    workspace_quality_unclaimed_residual_targets,
)


def _write_ts_tree(tmp_path: Path) -> None:
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "engine" / "simulation.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "src" / "engine" / "renderer.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "src" / "web.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "src" / "models" / "reputation.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "src" / "verify.ts").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "tests" / "verify.test.ts").write_text("export {}\n", encoding="utf-8")


_TSC_BLOB = """
> fairy-market-stall-management@1.0.0 build
> tsc -p tsconfig.json

src/engine/simulation.ts(140,5): error TS2554: Expected 1 arguments, but got 0.
src/engine/simulation.ts(141,5): error TS2345: Argument of type '{ id: Brand<string, "ItemId">; name: string; }' is not assignable to parameter of type 'ItemId'.
src/models/reputation.ts(150,13): error TS2322: Type 'ReputationTier' is not assignable to type 'never'.
src/web.ts(55,63): error TS2345: Argument of type 'Brand<string, string>' is not assignable to parameter of type 'CustomerId'.
"""

_NPM_TEST_BLOB = """
> fairy-market-stall-management@1.0.0 test
> tsx tests/verify.test.ts

not ok 7 - verifyProject fails fast when source file count drops below threshold (boundary)
  location: '/tmp/ws/tests/verify.test.ts:1:2478'
  error: "ENOENT: no such file or directory, scandir '/tmp/src'"
  stack: |-
    async filesUnder (/tmp/ws/src/verify.ts:11:19)
    async verifyProject (/tmp/ws/src/verify.ts:41:23)
    async TestContext.<anonymous> (/tmp/ws/tests/verify.test.ts:63:17)
"""


def test_typescript_tsc_leftover_rotates_off_claimed_engine_owner(tmp_path: Path) -> None:
    """After TASK-2 claims simulation/web, leftover must lease reputation.ts.

    Live L2-17 remint-3 R1 no_op claimed ``src/engine/simulation.ts``,
    ``src/web.ts``, ``src/engine/renderer.ts``. Scope filter deferred
    reputation.ts as owner_task_retry, but leftover_tus stayed empty.
    """

    _write_ts_tree(tmp_path)
    claimed = [
        "src/engine/simulation.ts",
        "src/web.ts",
        "src/engine/renderer.ts",
    ]
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [_TSC_BLOB, _NPM_TEST_BLOB],
        claimed_targets=claimed,
        workspace=tmp_path,
    )
    residual = workspace_quality_unclaimed_residual_targets(
        [_TSC_BLOB, _NPM_TEST_BLOB],
        claimed_targets=claimed,
        workspace=tmp_path,
    )
    assert leftover[0] == "src/models/reputation.ts"
    assert residual[0] == "src/models/reputation.ts"
    assert leftover_targets_should_force_owner_rotate(leftover, claimed)
    assert leftover_targets_should_force_owner_rotate(residual, claimed)
    assert "src/verify.ts" not in leftover
    assert "tests/verify.test.ts" not in leftover[:1]


def test_typescript_tsc_leftover_stays_when_only_claimed_compile_site_still_red(tmp_path: Path) -> None:
    """Claimed filter must not drop the only still-red tsc file."""

    _write_ts_tree(tmp_path)
    blob = "src/engine/simulation.ts(140,5): error TS2554: Expected 1 arguments, but got 0.\n"
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/engine/simulation.ts"],
        workspace=tmp_path,
    )
    assert leftover[0] == "src/engine/simulation.ts"
    assert not leftover_targets_should_force_owner_rotate(leftover, ["src/engine/simulation.ts"])


def test_typescript_npm_test_stack_leases_verify_when_tsc_is_green(tmp_path: Path) -> None:
    """Compile-green leftover must rotate onto the Node TAP owner path."""

    _write_ts_tree(tmp_path)
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [_NPM_TEST_BLOB],
        claimed_targets=["src/engine/simulation.ts"],
        workspace=tmp_path,
    )
    residual = workspace_quality_unclaimed_residual_targets(
        [_NPM_TEST_BLOB],
        claimed_targets=["src/engine/simulation.ts"],
        workspace=tmp_path,
    )
    assert leftover[0] == "src/verify.ts"
    assert residual[0] == "src/verify.ts"
    assert leftover_targets_should_force_owner_rotate(leftover, ["src/engine/simulation.ts"])
    assert leftover_targets_should_force_owner_rotate(residual, ["src/engine/simulation.ts"])


def test_javascript_tap_leftover_prefers_official_node_test(tmp_path: Path) -> None:
    """Official npm TAP must lease tests/product.test.js, not test_product.py.

    Live L2-18 remint-2 leftover was ``tests/test_product.py`` first. TASK-2
    could not take that helper, so ten rounds stayed on src/meteor.js.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "meteor.js").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "tests" / "product.test.js").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "not ok 1 - createMeteor returns a valid meteor in FALLING status\n"
        "  location: '/tmp/ws/tests/product.test.js:91:3'\n"
        "  stack: |-\n"
        "    TestContext.<anonymous> (/tmp/ws/tests/product.test.js:93:12)\n"
        "    createMeteor (/tmp/ws/src/meteor.js:80:10)\n"
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/meteor.js"],
        workspace=tmp_path,
    )
    assert leftover[0] == "tests/product.test.js"
    assert leftover_targets_should_force_owner_rotate(leftover, ["src/meteor.js"])
    residual = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["tests/product.test.js", "tests/test_product.py"],
        workspace=tmp_path,
    )
    assert residual[0] == "src/meteor.js"
    assert leftover_targets_should_force_owner_rotate(
        residual,
        ["tests/product.test.js", "tests/test_product.py"],
    )


def test_javascript_tap_location_without_stack_leases_callee_impl(tmp_path: Path) -> None:
    """TAP location-only residuals must map grantWish to src/wish.js after tests.

    Live L2-18 remint-5 residual_errors listed only
    ``location: tests/product.test.js:206`` and ``meteorId must be a
    non-empty string``. No stack frame named wish.js. After TASK-2 claimed
    the official TAP file, leftover stayed on tests and equal_count_swap
    was the only rotate signal.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "wish.js").write_text(
        "function grantWish(wish, meteorId) {\n"
        "  throw new TypeError('meteorId must be a non-empty string');\n"
        "}\n"
        "export { grantWish };\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "meteor.js").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "tests" / "product.test.js").write_text(
        "describe('wish lifecycle', () => {\n"
        "  test('open → close → grant lifecycle is observable', () => {\n"
        "    const granted = grantWish(closeWish(openWish(w)));\n"
        "    assert.equal(granted.status, 'granted');\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "not ok 2 - open → close → grant lifecycle is observable\n"
        "      ---\n"
        "      location: '/tmp/ws/tests/product.test.js:2:3'\n"
        "      failureType: 'testCodeFailure'\n"
        "      error: 'meteorId must be a non-empty string'\n"
        "      code: 'ERR_TEST_FAILURE'\n"
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert leftover[0] == "tests/product.test.js"
    residual = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["tests/product.test.js", "tests/test_product.py"],
        workspace=tmp_path,
    )
    assert residual[0] == "src/wish.js"
    assert leftover_targets_should_force_owner_rotate(
        residual,
        ["tests/product.test.js", "tests/test_product.py"],
    )
    after_tests = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["tests/product.test.js"],
        workspace=tmp_path,
    )
    assert after_tests[0] == "src/wish.js"


def test_javascript_tap_reference_error_leases_undeclared_assignment(tmp_path: Path) -> None:
    """TAP ``text is not defined`` must lease src/wish.js, not stay on tests.

    Live L2-18 remint-7 dropped ``const`` on ``text = clampString(...)``.
    TAP located the test() header; leftover kept rewriting tests.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "wish.js").write_text(
        "function validateWish(wish) {\n  text = clampString(wish.text, 'wish.text', 500);\n  return { text };\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "product.test.js").write_text(
        "test('createWish returns an OPEN wish', () => {\n  const w = makeWish();\n});\n",
        encoding="utf-8",
    )
    blob = (
        "not ok 1 - createWish returns an OPEN wish\n"
        "      ---\n"
        "      location: '/tmp/ws/tests/product.test.js:1:3'\n"
        "      failureType: 'testCodeFailure'\n"
        "      error: 'text is not defined'\n"
        "      code: 'ERR_TEST_FAILURE'\n"
        "      name: 'ReferenceError'\n"
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["tests/product.test.js"],
        workspace=tmp_path,
    )
    residual = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["tests/product.test.js"],
        workspace=tmp_path,
    )
    assert leftover[0] == "src/wish.js"
    assert residual[0] == "src/wish.js"


def test_javascript_syntax_error_keeps_official_tap_file(tmp_path: Path) -> None:
    """Unparseable official TAP must stay leased; do not rotate to impl.

    Live L2-18 remint-9 deleted the meteor describe opener. leftover then
    leased src/queue.js while node --test failed Unexpected token '}'.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "queue.js").write_text("export function enqueue() {}\n", encoding="utf-8")
    (tmp_path / "tests" / "product.test.js").write_text("test('x', () => {});\n});\n", encoding="utf-8")
    blob = (
        "Artifact quality scan failed: syntax error in tests/product.test.js: "
        "product.test.js:152\n});\n^\n\nSyntaxError: Unexpected token '}'\n"
        "not ok 1 - tests/product.test.js\n"
        "  location: '/tmp/ws/tests/product.test.js:1:1'\n"
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/queue.js"],
        workspace=tmp_path,
    )
    residual = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["src/queue.js"],
        workspace=tmp_path,
    )
    assert leftover[0] == "tests/product.test.js"
    assert residual[0] == "tests/product.test.js"


def test_javascript_leftover_demotes_python_helper_when_impl_remains(tmp_path: Path) -> None:
    """Claimed official TAP must rotate to src/index.js, not test_product.py.

    Live L2-18 remint-11 leftover after tests/product.test.js was
    tests/test_product.py first, so TASK-2 never released validateIndex.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "index.js").write_text(
        "function validateIndex(surface) { return surface; }\nexport { validateIndex };\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "queue.js").write_text("function enqueue() {}\nexport { enqueue };\n", encoding="utf-8")
    (tmp_path / "tests" / "product.test.js").write_text(
        "describe('index facade', () => {\n"
        "  test('validateIndex accepts the canonical index', () => {\n"
        "    assert.equal(validateIndex(index), index);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "not ok 9 - index facade\n"
        "      location: '/tmp/ws/tests/product.test.js:2:3'\n"
        "      error: 'Values have same structure but are not reference-equal'\n"
        "======================================================================\n"
        "FAIL: test_queue_enqueue_dequeue_round_trip "
        "(test_product.DomainRuntimeTests.test_queue_enqueue_dequeue_round_trip)\n"
        '  File "/tmp/ws/tests/test_product.py", line 10, in test_queue\n'
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["tests/product.test.js", "tests/test_product.py"],
        workspace=tmp_path,
    )
    assert leftover[0] == "src/index.js"


def test_javascript_official_npm_skips_python_unittest_helper(tmp_path: Path) -> None:
    """JS official quality is npm test, not tests/test_product.py unittest.

    Live L2-18 remint-12: npm build/test/start passed 36/36 TAP, then
    quality failed on python unittest discover of the leftover helper.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "index.js").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "tests" / "product.test.js").write_text("test('ok', () => {});\n", encoding="utf-8")
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "node -e \\"console.log(1)\\"", "test": "node --test tests/product.test.js", "start": "node -e \\"console.log(1)\\""}}\n',
        encoding="utf-8",
    )
    commands = WorkspaceQualityRunner(tmp_path).workspace_quality_commands({})
    assert ["npm", "test"] in commands
    assert not any(len(cmd) >= 4 and cmd[1:4] == ["-m", "unittest", "discover"] for cmd in commands)


def test_leftover_rotate_grants_one_extra_round_after_last_scheduled_slot() -> None:
    leftover = ["src/verify.ts"]
    claimed = ["src/engine/simulation.ts"]
    assert leftover_targets_should_force_owner_rotate(leftover, claimed)
    assert leftover_rotate_allows_quality_extra_round(
        round_index=8,
        max_rounds=8,
        leftover_extra_pending=True,
    )
    assert not leftover_rotate_allows_quality_extra_round(
        round_index=8,
        max_rounds=8,
        leftover_extra_pending=False,
    )
    assert not leftover_rotate_allows_quality_extra_round(
        round_index=10,
        max_rounds=8,
        leftover_extra_pending=True,
        extra_cap=2,
    )
