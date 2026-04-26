"""Contract tests for polaris.delivery.http.routers.cognitive_runtime module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import cognitive_runtime as cr_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(cr_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


def _make_ok_result(field_name: str, value: Any) -> MagicMock:
    result = MagicMock()
    result.ok = True
    setattr(result, field_name, value)
    result.error_code = None
    result.error_message = None
    return result


def _make_err_result(error_code: str = "not_found", error_message: str = "missing") -> MagicMock:
    result = MagicMock()
    result.ok = False
    result.error_code = error_code
    result.error_message = error_message
    return result


class TestCognitiveRuntimeRouter:
    """Contract tests for the cognitive runtime router."""

    def test_resolve_context_happy_path(self) -> None:
        """POST /cognitive-runtime/resolve-context returns 200 with snapshot."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.resolve_context.return_value = _make_ok_result("snapshot", {"ctx": True})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/resolve-context",
                json={
                    "workspace": ".",
                    "role": "pm",
                    "query": "test",
                    "session_id": "s1",
                    "run_id": "r1",
                    "mode": "test",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["snapshot"] == {"ctx": True}

    def test_resolve_context_not_found(self) -> None:
        """POST /cognitive-runtime/resolve-context returns 400 on service error."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.resolve_context.return_value = _make_err_result("not_found", "missing")

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/resolve-context",
                json={
                    "workspace": ".",
                    "role": "pm",
                    "query": "test",
                    "run_id": "r1",
                    "mode": "test",
                },
            )

        assert response.status_code == 404

    def test_lease_edit_scope_happy_path(self) -> None:
        """POST /cognitive-runtime/lease-edit-scope returns 200 with lease."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.lease_edit_scope.return_value = _make_ok_result("lease", {"id": "lease-1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/lease-edit-scope",
                json={"workspace": ".", "requested_by": "test", "scope_paths": ["a.py"]},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["lease"]["id"] == "lease-1"

    def test_validate_change_set_happy_path(self) -> None:
        """POST /cognitive-runtime/validate-change-set returns 200 with validation."""
        client = _build_client()
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.validation = {"valid": True}
        mock_result.error_code = None
        mock_result.error_message = None
        mock_service.validate_change_set.return_value = mock_result

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/validate-change-set",
                json={
                    "workspace": ".",
                    "changed_files": ["a.py"],
                    "allowed_scope_paths": ["a.py"],
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["validation"] == {"valid": True}

    def test_validate_change_set_failed(self) -> None:
        """POST /cognitive-runtime/validate-change-set returns 400 when validation is None."""
        client = _build_client()
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.validation = None
        mock_result.error_code = "validation_failed"
        mock_result.error_message = "Invalid"
        mock_service.validate_change_set.return_value = mock_result

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/validate-change-set",
                json={
                    "workspace": ".",
                    "changed_files": ["a.py"],
                    "allowed_scope_paths": ["a.py"],
                },
            )

        assert response.status_code == 400

    def test_record_runtime_receipt_happy_path(self) -> None:
        """POST /cognitive-runtime/runtime-receipts returns 200 with receipt."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.record_runtime_receipt.return_value = _make_ok_result("receipt", {"id": "r1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/runtime-receipts",
                json={"workspace": ".", "receipt_type": "test", "payload": {}},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["receipt"]["id"] == "r1"

    def test_get_runtime_receipt_happy_path(self) -> None:
        """GET /cognitive-runtime/runtime-receipts/{receipt_id} returns 200."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.get_runtime_receipt.return_value = _make_ok_result("receipt", {"id": "r1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.get("/cognitive-runtime/runtime-receipts/r1?workspace=.")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    def test_get_runtime_receipt_not_found(self) -> None:
        """GET /cognitive-runtime/runtime-receipts/{receipt_id} returns 404."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.get_runtime_receipt.return_value = _make_err_result("not_found", "missing")

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.get("/cognitive-runtime/runtime-receipts/r1?workspace=.")

        assert response.status_code == 404

    def test_export_handoff_pack_happy_path(self) -> None:
        """POST /cognitive-runtime/handoffs/export returns 200 with handoff."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.export_handoff_pack.return_value = _make_ok_result("handoff", {"id": "h1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/handoffs/export",
                json={"workspace": ".", "session_id": "s1"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["handoff"]["id"] == "h1"

    def test_rehydrate_handoff_pack_happy_path(self) -> None:
        """POST /cognitive-runtime/handoffs/rehydrate returns 200 with rehydration."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.rehydrate_handoff_pack.return_value = _make_ok_result("rehydration", {"id": "rh1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/handoffs/rehydrate",
                json={"workspace": ".", "handoff_id": "h1", "target_role": "pm"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    def test_map_diff_to_cells_happy_path(self) -> None:
        """POST /cognitive-runtime/map-diff-to-cells returns 200 with mapping."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.map_diff_to_cells.return_value = _make_ok_result("mapping", {"cells": ["c1"]})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/map-diff-to-cells",
                json={"workspace": ".", "changed_files": ["a.py"]},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["mapping"]["cells"] == ["c1"]

    def test_request_projection_compile_happy_path(self) -> None:
        """POST /cognitive-runtime/projection-compile returns 200 with request."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.request_projection_compile.return_value = _make_ok_result("request", {"id": "req1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/projection-compile",
                json={
                    "workspace": ".",
                    "requested_by": "test",
                    "subject_ref": "ref1",
                    "changed_files": ["a.py"],
                    "mapped_cells": ["c1"],
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    def test_promote_or_reject_happy_path(self) -> None:
        """POST /cognitive-runtime/promote-or-reject returns 200 with decision."""
        client = _build_client()
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.decision = {"action": "promote"}
        mock_result.error_code = None
        mock_result.error_message = None
        mock_service.promote_or_reject.return_value = mock_result

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/promote-or-reject",
                json={
                    "workspace": ".",
                    "subject_ref": "ref1",
                    "changed_files": ["a.py"],
                    "mapped_cells": ["c1"],
                    "projection_status": "pending",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["decision"]["action"] == "promote"

    def test_promote_or_reject_failed(self) -> None:
        """POST /cognitive-runtime/promote-or-reject returns 400 when decision is None."""
        client = _build_client()
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.decision = None
        mock_result.error_code = "promote_failed"
        mock_result.error_message = "Failed"
        mock_service.promote_or_reject.return_value = mock_result

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/promote-or-reject",
                json={
                    "workspace": ".",
                    "subject_ref": "ref1",
                    "changed_files": ["a.py"],
                    "mapped_cells": ["c1"],
                    "projection_status": "pending",
                },
            )

        assert response.status_code == 400

    def test_record_rollback_ledger_happy_path(self) -> None:
        """POST /cognitive-runtime/rollback-ledger returns 200 with entry."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.record_rollback_ledger.return_value = _make_ok_result("entry", {"id": "e1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/cognitive-runtime/rollback-ledger",
                json={"workspace": ".", "subject_ref": "ref1", "reason": "rollback"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["entry"]["id"] == "e1"

    def test_get_handoff_pack_happy_path(self) -> None:
        """GET /cognitive-runtime/handoffs/{handoff_id} returns 200."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.get_handoff_pack.return_value = _make_ok_result("handoff", {"id": "h1"})

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.get("/cognitive-runtime/handoffs/h1?workspace=.")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["handoff"]["id"] == "h1"

    def test_get_handoff_pack_not_found(self) -> None:
        """GET /cognitive-runtime/handoffs/{handoff_id} returns 404."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.get_handoff_pack.return_value = _make_err_result("not_found", "missing")

        with patch(
            "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
            return_value=mock_service,
        ):
            response = client.get("/cognitive-runtime/handoffs/h1?workspace=.")

        assert response.status_code == 404
