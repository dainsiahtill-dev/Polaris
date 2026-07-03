from __future__ import annotations

from polaris.infrastructure.accel.polaris_paths import (
    ACTIVE_RULES_URI,
    POLICY_URI,
    default_accel_runtime_home,
    resolve_polaris_paths,
)


def test_resolve_polaris_paths_exposes_current_policy_contract(tmp_path) -> None:
    paths = resolve_polaris_paths(tmp_path)

    assert paths.project_root == tmp_path.resolve()
    assert paths.policy_path == tmp_path.resolve() / "policy" / "polaris-agent-spec-v3.0.md"
    retired_field = "legacy" + "_policy_path"
    assert not hasattr(paths, retired_field)
    assert "v2.11" not in repr(paths)
    assert default_accel_runtime_home(tmp_path) == paths.accel_home


def test_accel_policy_uris_use_current_contract_names() -> None:
    assert POLICY_URI == "policy://polaris/agent-spec/v3.0"
    assert ACTIVE_RULES_URI == "polaris://policy/active_rules"
