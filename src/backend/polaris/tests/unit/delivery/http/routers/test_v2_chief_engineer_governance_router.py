"""Tests for the Tier-1 Chief Engineer governance routes.

Covers the Risk Register and Tech-Debt Ledger HTTP surface:
register / list / update-status for both, plus validation and UTF-8.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = Path(".")
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    from polaris.cells.runtime.state_owner.public.service import AppState
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.app_state = AppState(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch("polaris.bootstrap.assembly.assemble_core_services"),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch("polaris.delivery.http.app_factory.sync_process_settings_environment"),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.fixture
def workspace() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.mark.asyncio
async def test_register_and_list_risk(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/risks",
        params={"workspace": workspace},
        json={
            "task_id": "task-1",
            "title": "schema migration may drop legacy rows",
            "severity": "high",
            "owner": "chief_engineer",
            "mitigation": "stage behind a feature flag with dual-write",
            "links": ["ticket://DB-42"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    risk_id = data["risk"]["risk_id"]
    assert data["risk"]["severity"] == "high"
    assert data["risk"]["status"] == "open"

    listed = await client.get(
        "/v2/chief-engineer/risks",
        params={"workspace": workspace, "task_id": "task-1"},
    )
    assert listed.status_code == 200
    listed_data = listed.json()
    assert listed_data["total"] == 1
    assert listed_data["risks"][0]["risk_id"] == risk_id
    assert listed_data["summary"]["total"] == 1


@pytest.mark.asyncio
async def test_register_risk_invalid_severity_is_400(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/risks",
        params={"workspace": workspace},
        json={
            "task_id": "task-1",
            "title": "t",
            "severity": "apocalyptic",
            "owner": "chief_engineer",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_RISK_PAYLOAD"


@pytest.mark.asyncio
async def test_update_risk_status_round_trip(client: AsyncClient, workspace: str) -> None:
    created = await client.post(
        "/v2/chief-engineer/risks",
        params={"workspace": workspace},
        json={
            "task_id": "task-2",
            "title": "rate limit edge case",
            "severity": "blocker",
            "owner": "chief_engineer",
            "mitigation": "circuit breaker",
        },
    )
    risk_id = created.json()["risk"]["risk_id"]

    updated = await client.post(
        f"/v2/chief-engineer/risks/{risk_id}/status",
        params={"workspace": workspace},
        json={"status": "mitigating", "note": "flag staged"},
    )
    assert updated.status_code == 200
    assert updated.json()["risk"]["status"] == "mitigating"


@pytest.mark.asyncio
async def test_update_missing_risk_is_404(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/risks/risk_does_not_exist/status",
        params={"workspace": workspace},
        json={"status": "resolved"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RISK_NOT_FOUND"


@pytest.mark.asyncio
async def test_register_and_list_tech_debt_utf8(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/tech-debt",
        params={"workspace": workspace},
        json={
            "title": "手动 SQL 转义绕过 ORM",
            "description": "用户仓储层手写 IN 子句转义",
            "severity": "severe",
            "surface": "polaris/orm/user_repository.py",
            "owner": "chief_engineer",
            "evidence": ["git://abc123#diff-2"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    debt_id = data["tech_debt"]["debt_id"]
    assert data["tech_debt"]["title"] == "手动 SQL 转义绕过 ORM"

    listed = await client.get(
        "/v2/chief-engineer/tech-debt",
        params={"workspace": workspace, "surface": "polaris/orm/user_repository.py"},
    )
    assert listed.status_code == 200
    listed_data = listed.json()
    assert listed_data["total"] == 1
    assert listed_data["tech_debt"][0]["debt_id"] == debt_id


@pytest.mark.asyncio
async def test_update_tech_debt_status(client: AsyncClient, workspace: str) -> None:
    created = await client.post(
        "/v2/chief-engineer/tech-debt",
        params={"workspace": workspace},
        json={
            "title": "t",
            "description": "d",
            "severity": "minor",
            "surface": "s",
            "owner": "chief_engineer",
        },
    )
    debt_id = created.json()["tech_debt"]["debt_id"]

    updated = await client.post(
        f"/v2/chief-engineer/tech-debt/{debt_id}/status",
        params={"workspace": workspace},
        json={"status": "scheduled", "note": "sprint 14"},
    )
    assert updated.status_code == 200
    assert updated.json()["tech_debt"]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_update_missing_tech_debt_is_404(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/tech-debt/debt_missing/status",
        params={"workspace": workspace},
        json={"status": "paid"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TECH_DEBT_NOT_FOUND"


@pytest.mark.asyncio
async def test_risk_status_unsafe_single_segment_id_is_400(client: AsyncClient, workspace: str) -> None:
    # An id that reaches the handler but is not a bare safe token (here: a
    # space) must be rejected by the storage-layer guard (ValueError -> 400),
    # never reaching the filesystem.
    resp = await client.post(
        "/v2/chief-engineer/risks/bad%20id/status",
        params={"workspace": workspace},
        json={"status": "resolved"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_RISK_STATUS"


@pytest.mark.asyncio
async def test_risk_status_slash_traversal_is_not_processed(client: AsyncClient, workspace: str) -> None:
    # A slash-based traversal is normalized away by the router and never
    # matches a route; the key property is that it is NEVER processed (no 200)
    # and never touches the filesystem.
    resp = await client.post(
        "/v2/chief-engineer/risks/..%2F..%2F..%2Fetc%2Fpasswd/status",
        params={"workspace": workspace},
        json={"status": "resolved"},
    )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_tech_debt_status_unsafe_single_segment_id_is_400(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/tech-debt/bad%20id/status",
        params={"workspace": workspace},
        json={"status": "paid"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_TECH_DEBT_STATUS"


# ── Tier-2 governance: Architecture Decision Log ──────────────────────────


@pytest.mark.asyncio
async def test_register_and_list_adr_utf8(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/adrs",
        params={"workspace": workspace},
        json={
            "title": "采用单一事务内核",
            "decision": "所有 turn 提交走 TransactionKernel",
            "owner": "chief_engineer",
            "context": "多提交点导致部分写入",
            "consequences": "单一提交点；回滚更简单",
            "alternatives": ["每工具提交", "两阶段提交"],
            "related_task_ids": ["task-1"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    adr_id = data["adr"]["adr_id"]
    assert data["adr"]["status"] == "proposed"
    assert data["adr"]["title"] == "采用单一事务内核"

    listed = await client.get("/v2/chief-engineer/adrs", params={"workspace": workspace, "task_id": "task-1"})
    assert listed.status_code == 200
    listed_data = listed.json()
    assert listed_data["total"] == 1
    assert listed_data["adrs"][0]["adr_id"] == adr_id


@pytest.mark.asyncio
async def test_update_adr_status_round_trip(client: AsyncClient, workspace: str) -> None:
    created = await client.post(
        "/v2/chief-engineer/adrs",
        params={"workspace": workspace},
        json={"title": "t", "decision": "d", "owner": "ce"},
    )
    adr_id = created.json()["adr"]["adr_id"]
    updated = await client.post(
        f"/v2/chief-engineer/adrs/{adr_id}/status",
        params={"workspace": workspace},
        json={"status": "accepted", "note": "ratified"},
    )
    assert updated.status_code == 200
    assert updated.json()["adr"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_register_adr_invalid_status_query_is_400(client: AsyncClient, workspace: str) -> None:
    resp = await client.get(
        "/v2/chief-engineer/adrs",
        params={"workspace": workspace, "status": "nonsense"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ADR_QUERY"


@pytest.mark.asyncio
async def test_update_missing_adr_is_404(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/adrs/adr_missing/status",
        params={"workspace": workspace},
        json={"status": "accepted"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ADR_NOT_FOUND"


@pytest.mark.asyncio
async def test_adr_status_unsafe_single_segment_id_is_400(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/adrs/bad%20id/status",
        params={"workspace": workspace},
        json={"status": "accepted"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ADR_STATUS"


@pytest.mark.asyncio
async def test_adr_status_slash_traversal_is_not_processed(client: AsyncClient, workspace: str) -> None:
    resp = await client.post(
        "/v2/chief-engineer/adrs/..%2F..%2F..%2Fetc%2Fpasswd/status",
        params={"workspace": workspace},
        json={"status": "accepted"},
    )
    assert resp.status_code in (400, 404)
