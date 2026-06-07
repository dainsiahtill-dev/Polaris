from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.delivery.cli.pm import agents


def test_maybe_generate_agents_draft_falls_back_on_unexpected_runtime_exception(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "cache"
    workspace.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    draft_path = tmp_path / "runtime" / "AGENTS.generated.md"
    feedback_path = tmp_path / "runtime" / "AGENTS.feedback.md"

    def _raise_unexpected(*_args: Any, **_kwargs: Any) -> str:
        raise Exception("PM role runtime invocation failed: validation failed")

    monkeypatch.setattr(agents, "get_agents_draft_path", lambda *_args: str(draft_path))
    monkeypatch.setattr(agents, "get_agents_feedback_path", lambda *_args: str(feedback_path))
    monkeypatch.setattr(
        agents,
        "gather_docs_context",
        lambda *_args: ("docs requirement text", "root README text", "docs context"),
    )
    monkeypatch.setattr(agents, "get_template", lambda _name: "{docs_context}\n{feedback}")
    monkeypatch.setattr(agents, "render_template", lambda template, values: template.format(**values))
    monkeypatch.setattr(agents, "resolve_artifact_path", lambda *_args: str(tmp_path / "last-message.md"))
    monkeypatch.setattr(agents, "resolve_pm_backend_kind", lambda *_args: ("role-runtime", None))
    monkeypatch.setattr(agents, "ensure_pm_backend_available", lambda _backend: None)
    monkeypatch.setattr(agents, "invoke_pm_backend", _raise_unexpected)

    result = agents.maybe_generate_agents_draft(
        str(workspace),
        str(cache_root),
        "2026-06-07T00:00:00",
        SimpleNamespace(
            model="gemma-4-12B-it-Q8_0",
            pm_show_output=False,
            prompt_profile="",
            pm_backend="auto",
        ),
    )

    assert result == str(draft_path)
    content = draft_path.read_text(encoding="utf-8")
    assert "<INSTRUCTIONS>" in content
    assert "fallback_reason: PM role runtime invocation failed: validation failed" in content
