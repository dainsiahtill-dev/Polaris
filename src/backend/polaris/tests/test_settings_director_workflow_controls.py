from __future__ import annotations

from polaris.bootstrap.config import Settings, SettingsUpdate


def test_settings_apply_update_persists_director_workflow_controls(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    settings.apply_update(
        SettingsUpdate(
            director_execution_mode="serial",
            director_max_parallel_tasks=5,
            director_ready_timeout_seconds=31,
            director_claim_timeout_seconds=32,
            director_phase_timeout_seconds=330,
            director_complete_timeout_seconds=34,
            director_task_timeout_seconds=1800,
        )
    )

    payload = settings.to_payload()
    assert payload["director_execution_mode"] == "serial"
    assert payload["director_max_parallel_tasks"] == 5
    assert payload["director_ready_timeout_seconds"] == 31
    assert payload["director_claim_timeout_seconds"] == 32
    assert payload["director_phase_timeout_seconds"] == 330
    assert payload["director_complete_timeout_seconds"] == 34
    assert payload["director_task_timeout_seconds"] == 1800


def test_settings_update_normalizes_invalid_director_workflow_controls(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    settings.apply_update(
        SettingsUpdate(
            director_execution_mode="invalid",
            director_max_parallel_tasks=0,
            director_ready_timeout_seconds=0,
            director_claim_timeout_seconds=0,
            director_phase_timeout_seconds=0,
            director_complete_timeout_seconds=0,
            director_task_timeout_seconds=0,
        )
    )

    assert settings.director_execution_mode == "parallel"
    assert settings.director_max_parallel_tasks == 1
    assert settings.director_ready_timeout_seconds == 1
    assert settings.director_claim_timeout_seconds == 1
    assert settings.director_phase_timeout_seconds == 1
    assert settings.director_complete_timeout_seconds == 1
    assert settings.director_task_timeout_seconds == 1


def test_settings_apply_update_persists_audit_llm_controls(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    settings.apply_update(
        SettingsUpdate(
            audit_llm_enabled=True,
            audit_llm_role="qa",
            audit_llm_timeout=240,
            audit_llm_prefer_local_ollama=True,
            audit_llm_allow_remote_fallback=False,
        )
    )

    payload = settings.to_payload()
    assert payload["audit_llm_enabled"] is True
    assert payload["audit_llm_role"] == "qa"
    assert payload["audit_llm_timeout"] == 240
    assert payload["audit_llm_prefer_local_ollama"] is True
    assert payload["audit_llm_allow_remote_fallback"] is False


def test_settings_update_normalizes_invalid_audit_llm_controls(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    settings.apply_update(
        SettingsUpdate(
            audit_llm_role="",
            audit_llm_timeout=0,
        )
    )

    assert settings.audit_llm_role == "qa"
    assert settings.audit_llm_timeout == 30
