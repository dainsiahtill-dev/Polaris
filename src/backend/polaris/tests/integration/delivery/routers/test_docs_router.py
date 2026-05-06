"""Contract tests for polaris.delivery.http.routers.docs module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import docs as docs_router
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.error_handlers import setup_exception_handlers


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(docs_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestDocsRouter:
    """Contract tests for the docs router."""

    def test_docs_init_dialogue_happy_path(self) -> None:
        """POST /docs/init/dialogue returns 200 with reply and fields."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
                return_value={
                    "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                    "providers": {"p1": {"type": "openai", "base_url": "", "api_path": "", "api_key": ""}},
                },
            ),
            patch(
                "polaris.delivery.http.routers.docs.generate_docs_dialogue_turn",
                new_callable=AsyncMock,
                return_value={
                    "reply": "Hello",
                    "questions": [],
                    "tiaochen": [],
                    "meta": {},
                    "handoffs": {},
                    "fields": {"goal": "test"},
                },
            ),
        ):
            response = client.post(
                "/docs/init/dialogue",
                json={
                    "message": "hi",
                    "goal": "",
                    "in_scope": "",
                    "out_of_scope": "",
                    "constraints": "",
                    "definition_of_done": "",
                    "backlog": "",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["reply"] == "Hello"
        assert payload["fields"]["goal"] == "test"

    def test_docs_init_dialogue_llm_failure(self) -> None:
        """POST /docs/init/dialogue returns 409 when LLM fails."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
                return_value={
                    "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                    "providers": {"p1": {"type": "openai", "base_url": "", "api_path": "", "api_key": ""}},
                },
            ),
            patch(
                "polaris.delivery.http.routers.docs.generate_docs_dialogue_turn",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                "/docs/init/dialogue",
                json={
                    "message": "hi",
                    "goal": "",
                    "in_scope": "",
                    "out_of_scope": "",
                    "constraints": "",
                    "definition_of_done": "",
                    "backlog": "",
                },
            )

        assert response.status_code == 409

    def test_docs_init_suggest_happy_path(self) -> None:
        """POST /docs/init/suggest returns 200 with AI fields."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
                return_value={
                    "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                    "providers": {"p1": {"type": "openai", "base_url": "", "api_path": "", "api_key": ""}},
                },
            ),
            patch(
                "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
                new_callable=AsyncMock,
                return_value={
                    "goal": ["g1"],
                    "in_scope": ["i1"],
                    "out_of_scope": ["o1"],
                    "constraints": ["c1"],
                    "definition_of_done": ["d1"],
                    "backlog": ["b1"],
                },
            ),
        ):
            response = client.post(
                "/docs/init/suggest",
                json={
                    "goal": "",
                    "in_scope": "",
                    "out_of_scope": "",
                    "constraints": "",
                    "definition_of_done": "",
                    "backlog": "",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["fields"]["goal"] == "g1"

    def test_docs_init_suggest_llm_unavailable(self) -> None:
        """POST /docs/init/suggest returns 409 when LLM unavailable."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
                return_value={
                    "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                    "providers": {"p1": {"type": "openai", "base_url": "", "api_path": "", "api_key": ""}},
                },
            ),
            patch(
                "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.post(
                "/docs/init/suggest",
                json={
                    "goal": "",
                    "in_scope": "",
                    "out_of_scope": "",
                    "constraints": "",
                    "definition_of_done": "",
                    "backlog": "",
                },
            )

        assert response.status_code == 409

    def test_docs_init_preview_happy_path(self) -> None:
        """POST /docs/init/preview returns 200 with file list."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
                return_value={
                    "roles": {"architect": {"provider_id": "p1", "model": "m1"}},
                    "providers": {"p1": {"type": "openai", "base_url": "", "api_path": "", "api_key": ""}},
                },
            ),
            patch(
                "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
                new_callable=AsyncMock,
                return_value={"goal": ["g1"]},
            ),
            patch(
                "polaris.delivery.http.routers.docs.detect_project_profile",
                return_value={"python": True, "node": False, "go": False, "rust": False, "package_manager": None},
            ),
            patch(
                "polaris.delivery.http.routers.docs.build_docs_templates",
                return_value={"docs/SPEC.md": "# Spec"},
            ),
            patch(
                "polaris.delivery.http.routers.docs.select_docs_target_root",
                return_value="docs",
            ),
            patch(
                "polaris.delivery.http.routers.docs.workspace_has_docs",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.docs.resolve_artifact_path",
                return_value="/tmp/workspace/docs/SPEC.md",
            ),
            patch(
                "polaris.delivery.http.routers.docs.os.path.isfile",
                return_value=False,
            ),
        ):
            response = client.post(
                "/docs/init/preview",
                json={
                    "mode": "minimal",
                    "goal": "",
                    "in_scope": "",
                    "out_of_scope": "",
                    "constraints": "",
                    "definition_of_done": "",
                    "backlog": "",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert len(payload["files"]) == 1
        assert payload["files"][0]["path"] == "docs/SPEC.md"

    def test_docs_init_apply_happy_path(self) -> None:
        """POST /docs/init/apply returns 200 with created files."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.normalize_rel_path",
                return_value="workspace/docs/SPEC.md",
            ),
            patch(
                "polaris.delivery.http.routers.docs.is_safe_docs_path",
                return_value=True,
            ),
            patch(
                "polaris.delivery.http.routers.docs.resolve_artifact_path",
                return_value="/tmp/workspace/docs/SPEC.md",
            ),
            patch(
                "polaris.delivery.http.routers.docs.write_text_atomic",
            ) as mock_write,
            patch(
                "polaris.delivery.http.routers.docs.workspace_has_docs",
                return_value=True,
            ),
            patch(
                "polaris.delivery.http.routers.docs.clear_workspace_status",
            ),
            patch(
                "polaris.delivery.http.routers.docs.emit_event",
            ),
        ):
            response = client.post(
                "/docs/init/apply",
                json={
                    "target_root": "workspace/docs",
                    "files": [{"path": "workspace/docs/SPEC.md", "content": "# Spec"}],
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "workspace/docs/SPEC.md" in payload["files"]
        mock_write.assert_called_once()

    def test_docs_init_apply_invalid_target_root(self) -> None:
        """POST /docs/init/apply returns 400 for invalid target_root."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.docs.normalize_rel_path",
                return_value="invalid/path",
            ),
        ):
            response = client.post(
                "/docs/init/apply",
                json={
                    "target_root": "invalid/path",
                    "files": [{"path": "invalid/path/SPEC.md", "content": "# Spec"}],
                },
            )

        assert response.status_code == 400
        assert "target_root" in response.json()["error"]["message"].lower()

    def test_docs_init_apply_no_files(self) -> None:
        """POST /docs/init/apply returns 400 when no files provided."""
        client = _build_client()
        response = client.post(
            "/docs/init/apply",
            json={"target_root": "workspace/docs", "files": []},
        )
        assert response.status_code == 400
        assert "no files" in response.json()["error"]["message"].lower()
