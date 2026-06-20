"""Static guard for Polaris realtime single-rail transport."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
FRONTEND_SRC = REPO_ROOT / "src" / "frontend" / "src"
BACKEND_ROUTERS = REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "http" / "routers"
BACKEND_RUNTIME_WS = REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "ws"
BACKEND_PRODUCT_REALTIME_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "ws" / "endpoints" / "websocket_core.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "ws" / "endpoints" / "websocket_loop.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "ws" / "endpoints" / "client_message.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "infrastructure" / "log_pipeline" / "writer.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "infrastructure" / "log_pipeline" / "__init__.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "runtime" / "projection" / "internal" / "workflow_status.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "roles" / "runtime" / "internal" / "process_service.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "kernelone" / "events" / "file_event_broadcaster.py",
)
REQUIRED_REALTIME_POLICY_DOCS = (
    REPO_ROOT / "src" / "backend" / "AGENTS.md",
    REPO_ROOT / "src" / "backend" / "docs" / "AGENT_ARCHITECTURE_STANDARD.md",
)
ACTIVE_REALTIME_DOCS = (
    REPO_ROOT / "docs" / "product" / "requirements.md",
    REPO_ROOT / "src" / "backend" / "API_AUDIT_REPORT.md",
    REPO_ROOT / "src" / "backend" / "docs" / "API_STANDARDIZATION_CHANGELOG.md",
    REPO_ROOT / "src" / "backend" / "docs" / "API_DEVELOPER_ONBOARDING.md",
    REPO_ROOT / "src" / "backend" / "docs" / "API_VERSIONING_GUIDE.md",
    REPO_ROOT / "src" / "backend" / "docs" / "API_V1_TO_V2_MIGRATION.md",
    REPO_ROOT / "src" / "backend" / "docs" / "API_V2_QUICK_REFERENCE.md",
)

FRONTEND_FORBIDDEN = (
    "EventSource",
    "text/event-stream",
    "useWebSocketWithFallback",
    "useRuntimeWebSocketWithFallback",
    "useCourtWebSocketWithFallback",
    "fallbackInterval",
    "maxFallbackAttempts",
    "pollInterval",
    "longPolling",
    "long-poll",
    "ReadableStream",
    "getReader(",
)

BACKEND_ROUTER_FORBIDDEN = (
    "StreamingResponse",
    "text/event-stream",
)

BACKEND_PRODUCT_REALTIME_FORBIDDEN = (
    "REALTIME_SIGNAL_HUB",
    "RUNTIME_EVENT_FANOUT",
    "LOG_REALTIME_FANOUT",
    "RealtimeLogSubscription",
    "wait_for_update(",
    "ensure_watch(",
    "send_all_snapshots",
    "send_incrementals",
    "process_local",
    "next_message(timeout=0.1",
)

ACTIVE_DOC_FORBIDDEN_ADVERTISING = (
    "SSE event types",
    "SSE Event Changes",
    "SSE Event Type Unification",
    "All SSE endpoints",
    "JetStream SSE Consumer",
    "SSE helpers",
    "SSE utilities",
    "WS/SSE",
    "supports SSE",
    "SSE stream with",
    "SSE streaming normalized",
    "HTTP polling fallback",
    "long-polling fallback",
    "timer fetch loop as realtime",
    "file polling as realtime",
    "支持 SSE",
    "SSE 流式",
    "轮询兜底",
)

REQUIRED_POLICY_PHRASES = (
    "Nat-JetStream",
    "/v2/ws/runtime",
    "禁止轮询",
    "HTTP long polling",
    "file polling",
)

UI_TIMER_ALLOWLIST = {
    FRONTEND_SRC / "app" / "components" / "EnhancedNotificationManager.tsx",
    FRONTEND_SRC / "app" / "components" / "LlmRuntimeOverlay.tsx",
    FRONTEND_SRC / "app" / "components" / "ai-dialogue" / "ManusStyleStatusIndicator.tsx",
    FRONTEND_SRC / "app" / "components" / "RealTimeStatusBar.tsx",
    # Runtime WebSocket heartbeat only; HTTP/file data polling remains forbidden.
    FRONTEND_SRC / "runtime" / "transport" / "runtimeSocketManager.ts",
}


def _product_frontend_files() -> list[Path]:
    return [
        path
        for path in FRONTEND_SRC.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx"}
        and "__tests__" not in path.parts
        and ".test." not in path.name
    ]


def test_frontend_realtime_uses_runtime_transport_single_entrypoint() -> None:
    """Product frontend code must not restore SSE, stream readers, or polling fallback."""

    findings: list[str] = []
    websocket_hits: list[Path] = []
    timer_hits: list[Path] = []

    for path in _product_frontend_files():
        text = path.read_text(encoding="utf-8")
        for token in FRONTEND_FORBIDDEN:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")
        if "new WebSocket" in text:
            websocket_hits.append(path)
        if "setInterval(" in text:
            timer_hits.append(path)

    assert findings == []
    assert websocket_hits == [FRONTEND_SRC / "api.ts"]
    assert set(timer_hits).issubset(UI_TIMER_ALLOWLIST)


def test_backend_http_routers_do_not_expose_sse_streams() -> None:
    """Legacy stream routes must fail closed instead of returning SSE responses."""

    findings: list[str] = []
    for path in BACKEND_ROUTERS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in BACKEND_ROUTER_FORBIDDEN:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_backend_product_realtime_has_no_legacy_push_or_polling_sources() -> None:
    """Product realtime must be JetStream -> runtime.v2 WebSocket only."""

    findings: list[str] = []
    for path in BACKEND_PRODUCT_REALTIME_FILES:
        text = path.read_text(encoding="utf-8")
        for token in BACKEND_PRODUCT_REALTIME_FORBIDDEN:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_active_docs_do_not_advertise_sse_or_polling_realtime() -> None:
    """Active docs must encode the single realtime rail and reject polling designs."""

    findings: list[str] = []
    for path in ACTIVE_REALTIME_DOCS:
        text = path.read_text(encoding="utf-8")
        for token in ACTIVE_DOC_FORBIDDEN_ADVERTISING:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} advertises {token!r}")

    assert findings == []


def test_realtime_policy_docs_make_no_polling_constraint_explicit() -> None:
    """Core agent docs must keep the no-polling realtime constraint discoverable."""

    findings: list[str] = []
    for path in REQUIRED_REALTIME_POLICY_DOCS:
        text = path.read_text(encoding="utf-8")
        for phrase in REQUIRED_POLICY_PHRASES:
            if phrase not in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} missing {phrase!r}")

    assert findings == []
