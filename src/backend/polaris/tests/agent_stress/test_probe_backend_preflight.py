from __future__ import annotations

from tests.agent_stress.probe import (
    ProbeReport,
    ProbeStatus,
    RoleProbeResult,
    _build_preflight_blocked_report,
)


def test_probe_report_serializes_backend_preflight_and_context() -> None:
    report = ProbeReport(
        timestamp="2026-03-23T17:50:00",
        overall_status=ProbeStatus.HEALTHY,
        role_results=[
            RoleProbeResult(
                role="qa",
                status=ProbeStatus.HEALTHY,
                provider="demo-provider",
                model="demo-model",
            )
        ],
        backend_preflight={
            "status": "healthy",
            "ws_runtime_v2_accessible": True,
            "jetstream_accessible": True,
            "projection_transport": "ws.runtime_v2",
        },
        backend_context={
            "source": "terminal-auto-bootstrap",
            "backend_url": "http://127.0.0.1:49977",
        },
    )

    payload = report.to_dict()

    assert payload["backend_preflight"]["status"] == "healthy"
    assert payload["backend_preflight"]["ws_runtime_v2_accessible"] is True
    assert payload["backend_preflight"]["jetstream_accessible"] is True
    assert payload["backend_context"]["source"] == "terminal-auto-bootstrap"
    assert "WS runtime.v2 可用: True" in report.to_markdown()
    assert "JetStream 可用: True" in report.to_markdown()


def test_build_preflight_blocked_report_preserves_projection_failure_details() -> None:
    blocked = _build_preflight_blocked_report(
        timestamp="2026-03-23T17:51:00",
        backend_context={
            "source": "terminal-auto-bootstrap",
            "backend_url": "http://127.0.0.1:49977",
        },
        preflight_report={
            "status": "runtime_v2_unavailable",
            "ws_runtime_v2_accessible": True,
            "jetstream_accessible": False,
            "projection_transport": "none",
        },
    )

    assert blocked["blocking_reason"] == "backend_preflight_failed"
    assert blocked["backend_preflight"]["status"] == "runtime_v2_unavailable"
    assert blocked["backend_preflight"]["ws_runtime_v2_accessible"] is True
    assert blocked["backend_preflight"]["jetstream_accessible"] is False
