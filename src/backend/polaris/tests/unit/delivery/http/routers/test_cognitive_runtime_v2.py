"""Tests for Polaris v2 Cognitive Runtime router.

Covers v2 cognitive-runtime endpoints that delegate to legacy handlers.
External services are mocked to avoid runtime and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeSnapshot:
    query: str = "test-query"
    role: str = "pm"


@dataclass
class _FakeLease:
    lease_id: str = "lease-123"


@dataclass
class _FakeValidation:
    ok: bool = True


@dataclass
class _FakeReceipt:
    receipt_id: str = "receipt-123"


@dataclass
class _FakeHandoff:
    handoff_id: str = "handoff-123"


@dataclass
class _FakeRehydration:
    session_id: str = "session-123"


@dataclass
class _FakeMapping:
    cells: tuple[str, ...] = ("cell-a",)


@dataclass
class _FakeProjection:
    request_id: str = "proj-123"


@dataclass
class _FakeDecision:
    decision: str = "promote"


@dataclass
class _FakeRollback:
    entry_id: str = "rollback-123"


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/resolve-context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_resolve_context(client: AsyncClient) -> None:
    """V2 resolve-context should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.resolve_context.return_value = MagicMock(
            ok=True,
            snapshot=_FakeSnapshot(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/resolve-context",
            json={
                "workspace": ".",
                "role": "pm",
                "query": "test",
                "step": 1,
                "run_id": "run-1",
                "mode": "interactive",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["snapshot"]["query"] == "test-query"
        mock_service.resolve_context.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/lease-edit-scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_lease_edit_scope(client: AsyncClient) -> None:
    """V2 lease-edit-scope should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.lease_edit_scope.return_value = MagicMock(
            ok=True,
            lease=_FakeLease(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/lease-edit-scope",
            json={
                "workspace": ".",
                "requested_by": "user",
                "scope_paths": ["src/foo.py"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["lease"]["lease_id"] == "lease-123"
        mock_service.lease_edit_scope.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/validate-change-set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_validate_change_set(client: AsyncClient) -> None:
    """V2 validate-change-set should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.validate_change_set.return_value = MagicMock(
            ok=True,
            validation=_FakeValidation(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/validate-change-set",
            json={
                "workspace": ".",
                "changed_files": ["src/foo.py"],
                "allowed_scope_paths": ["src/"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["validation"]["ok"] is True
        mock_service.validate_change_set.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/runtime-receipts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_record_runtime_receipt(client: AsyncClient) -> None:
    """V2 runtime-receipts should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.record_runtime_receipt.return_value = MagicMock(
            ok=True,
            receipt=_FakeReceipt(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/runtime-receipts",
            json={
                "workspace": ".",
                "receipt_type": "test",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["receipt"]["receipt_id"] == "receipt-123"
        mock_service.record_runtime_receipt.assert_called_once()


# ---------------------------------------------------------------------------
# GET /v2/cognitive-runtime/runtime-receipts/{receipt_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_runtime_receipt(client: AsyncClient) -> None:
    """V2 get runtime-receipt should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.get_runtime_receipt.return_value = MagicMock(
            ok=True,
            receipt=_FakeReceipt(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.get(
            "/cognitive-runtime/v2/cognitive-runtime/runtime-receipts/receipt-123?workspace=.",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["receipt"]["receipt_id"] == "receipt-123"
        mock_service.get_runtime_receipt.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/handoffs/export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_export_handoff_pack(client: AsyncClient) -> None:
    """V2 handoffs/export should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.export_handoff_pack.return_value = MagicMock(
            ok=True,
            handoff=_FakeHandoff(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/handoffs/export",
            json={
                "workspace": ".",
                "session_id": "session-1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["handoff"]["handoff_id"] == "handoff-123"
        mock_service.export_handoff_pack.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/handoffs/rehydrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_rehydrate_handoff_pack(client: AsyncClient) -> None:
    """V2 handoffs/rehydrate should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.rehydrate_handoff_pack.return_value = MagicMock(
            ok=True,
            rehydration=_FakeRehydration(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/handoffs/rehydrate",
            json={
                "workspace": ".",
                "handoff_id": "handoff-1",
                "target_role": "pm",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["rehydration"]["session_id"] == "session-123"
        mock_service.rehydrate_handoff_pack.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/map-diff-to-cells
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_map_diff_to_cells(client: AsyncClient) -> None:
    """V2 map-diff-to-cells should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.map_diff_to_cells.return_value = MagicMock(
            ok=True,
            mapping=_FakeMapping(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/map-diff-to-cells",
            json={
                "workspace": ".",
                "changed_files": ["src/foo.py"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["mapping"]["cells"] == ["cell-a"]
        mock_service.map_diff_to_cells.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/projection-compile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_projection_compile(client: AsyncClient) -> None:
    """V2 projection-compile should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.request_projection_compile.return_value = MagicMock(
            ok=True,
            request=_FakeProjection(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/projection-compile",
            json={
                "workspace": ".",
                "requested_by": "user",
                "subject_ref": "ref-1",
                "changed_files": ["src/foo.py"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["request"]["request_id"] == "proj-123"
        mock_service.request_projection_compile.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/promote-or-reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_promote_or_reject(client: AsyncClient) -> None:
    """V2 promote-or-reject should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.promote_or_reject.return_value = MagicMock(
            ok=True,
            decision=_FakeDecision(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/promote-or-reject",
            json={
                "workspace": ".",
                "subject_ref": "ref-1",
                "changed_files": ["src/foo.py"],
                "mapped_cells": ["cell-a"],
                "write_gate_allowed": True,
                "projection_status": "compiled",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["decision"]["decision"] == "promote"
        mock_service.promote_or_reject.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/cognitive-runtime/rollback-ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_rollback_ledger(client: AsyncClient) -> None:
    """V2 rollback-ledger should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.record_rollback_ledger.return_value = MagicMock(
            ok=True,
            entry=_FakeRollback(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.post(
            "/cognitive-runtime/v2/cognitive-runtime/rollback-ledger",
            json={
                "workspace": ".",
                "subject_ref": "ref-1",
                "reason": "rollback test",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["entry"]["entry_id"] == "rollback-123"
        mock_service.record_rollback_ledger.assert_called_once()


# ---------------------------------------------------------------------------
# GET /v2/cognitive-runtime/handoffs/{handoff_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_handoff_pack(client: AsyncClient) -> None:
    """V2 get handoff-pack should return 200 and delegate to the service."""
    with patch(
        "polaris.delivery.http.routers.cognitive_runtime.get_cognitive_runtime_public_service",
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.get_handoff_pack.return_value = MagicMock(
            ok=True,
            handoff=_FakeHandoff(),
            error_code=None,
            error_message=None,
        )
        mock_get_service.return_value = mock_service

        response = await client.get(
            "/cognitive-runtime/v2/cognitive-runtime/handoffs/handoff-123?workspace=.",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["handoff"]["handoff_id"] == "handoff-123"
        mock_service.get_handoff_pack.assert_called_once()
