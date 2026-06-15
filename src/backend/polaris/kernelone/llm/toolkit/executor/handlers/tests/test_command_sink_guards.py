from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


def test_execute_command_input_redirection_must_stay_inside_workspace(tmp_path: Path) -> None:
    outside_secret = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside_secret.write_text("outside-secret", encoding="utf-8")

    executor = AgentAccelToolExecutor(workspace=str(tmp_path))
    result = executor.execute(
        "execute_command",
        {
            "command": f"python3 --version < {outside_secret}",
            "timeout": 5,
        },
    )

    assert result["ok"] is False
    assert result.get("blocked") is True
    assert result.get("handler_error_type") == "input_redirection_outside_workspace"
    assert "outside-secret" not in str(result)
