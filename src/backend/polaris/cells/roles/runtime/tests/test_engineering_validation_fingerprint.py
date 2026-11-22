from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import (
    failure_signatures,
    validation_key,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation


def test_validation_key_separates_input_from_symptoms() -> None:
    binding = {"commands": ["node tests/run-tests.js"], "toolchain": "node18"}
    first = validation_key(commands=["node tests/run-tests.js"], workspace_revision=4, input_binding=binding)
    second = validation_key(commands=["node tests/run-tests.js"], workspace_revision=4, input_binding=binding)
    changed = validation_key(commands=["node tests/run-tests.js"], workspace_revision=5, input_binding=binding)
    assert first == second and first != changed


def test_validation_key_includes_commands_and_binding() -> None:
    base = validation_key(commands=["a"], workspace_revision=1, input_binding={"x": 1})
    assert base != validation_key(commands=["b"], workspace_revision=1, input_binding={"x": 1})
    assert base != validation_key(commands=["a"], workspace_revision=1, input_binding={"x": 2})


def test_failure_signatures_are_not_cache_keys() -> None:
    checks = (
        ToolObservation(
            "v1",
            "verify_project",
            True,
            json.dumps({"command_card": {"stdout_preview": "[FAIL] 交互：刹车无效"}}),
            1,
            False,
        ),
        ToolObservation(
            "v2",
            "verify_project",
            True,
            json.dumps({"command_card": {"stdout_preview": "[PASS] 启动正常"}}),
            0,
            False,
        ),
    )
    signatures = failure_signatures(checks)
    assert signatures == ["[FAIL] 交互：刹车无效"]
