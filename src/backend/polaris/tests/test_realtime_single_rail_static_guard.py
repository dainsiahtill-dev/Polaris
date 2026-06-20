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
FRONTEND_WORKSPACE_REALTIME_FILES = (
    FRONTEND_SRC / "app" / "App.tsx",
    FRONTEND_SRC / "app" / "components" / "ControlPanel.tsx",
    FRONTEND_SRC / "app" / "components" / "pm" / "PMWorkspace.tsx",
    FRONTEND_SRC / "app" / "components" / "chief-engineer" / "ChiefEngineerWorkspace.tsx",
    FRONTEND_SRC / "app" / "components" / "director" / "DirectorWorkspace.tsx",
    FRONTEND_SRC / "app" / "hooks" / "useLiveTaskQueues.ts",
    FRONTEND_SRC / "app" / "hooks" / "useRuntime.ts",
    FRONTEND_SRC / "app" / "hooks" / "useRuntimeConnection.ts",
    FRONTEND_SRC / "hooks" / "useFactory.ts",
    FRONTEND_SRC / "hooks" / "useFactoryBench.ts",
    FRONTEND_SRC / "hooks" / "useProcessOperations.ts",
)
TASK_MARKET_EVENT_WAKE_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "runtime" / "task_market" / "internal" / "consumer_loop.py",
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "chief_engineer"
    / "blueprint"
    / "internal"
    / "ce_consumer.py",
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "director"
    / "task_consumer"
    / "internal"
    / "director_consumer.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "qa" / "audit_verdict" / "internal" / "qa_consumer.py",
)
EVENT_WAKE_AGENT_BUS_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "roles" / "runtime" / "internal" / "bus_port.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "kernelone" / "multi_agent" / "neural_syndicate" / "base_agent.py",
)
ROLE_WORKER_POOL_EVENT_WAKE_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "runtime" / "task_runtime" / "internal" / "task_board.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "roles" / "runtime" / "internal" / "worker_pool.py",
)
PM_DISPATCH_WORKER_POOL_EVENT_WAKE_FILES = (
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "pm_dispatch"
    / "internal"
    / "dispatch"
    / "worker_pool.py",
)
DIRECTOR_EXECUTION_EVENT_WAKE_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "cells" / "director" / "execution" / "service.py",
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

FRONTEND_WORKSPACE_FORBIDDEN_SNAPSHOT_REFRESH = (
    "apiFetchFresh('/state/snapshot'",
    'apiFetchFresh("/state/snapshot"',
    "refreshProgressSnapshot",
    "getPmStatus(",
    "getDirectorStatus(",
    "'/v2/pm/status'",
    '"/v2/pm/status"',
    "'/v2/director/status",
    '"/v2/director/status',
)

TASK_MARKET_FORBIDDEN_INTERVAL_WAKEUPS = (
    "_stop_event.wait(self._poll_interval",
    "_work_event.wait(self._poll_interval",
    "_outbox_event.wait(self._outbox_relay_interval",
    "_outbox_event.wait(self._outbox_relay_idle_timeout",
    "time.sleep(self._poll_interval",
)

AGENT_BUS_FORBIDDEN_INTERVAL_WAKEUPS = (
    "asyncio.sleep(bounded_sleep",
    "time.sleep(interval",
    "_MAX_CANCEL_DELAY_SEC",
    "safe_interval",
    "block=False,\n                    timeout=self._mailbox_poll_interval",
)

DIRECTOR_EXECUTION_FORBIDDEN_INTERVAL_WAKEUPS = (
    "task_poll_interval",
    "await asyncio.wait_for(self._stop_event.wait()",
    "asyncio.sleep(self.config",
    "time.sleep(self.config",
)

ROLE_WORKER_POOL_FORBIDDEN_INTERVAL_WAKEUPS = (
    "get(timeout=1",
    "timeout=1.0",
    "time.sleep(self.config.poll_interval",
    "await asyncio.sleep(self.config.poll_interval",
    "idle_time += self.config.poll_interval",
    "Supports idle/poll mechanism",
)

PM_DISPATCH_WORKER_POOL_FORBIDDEN_INTERVAL_WAKEUPS = (
    "time.sleep(poll_interval",
    "time.sleep(min(0.2",
    "Yield one poll_interval",
    "poll_interval: idle back-off",
    "a sibling is still executing; it may unblock a step",
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


def test_frontend_workspaces_do_not_refresh_status_snapshots_for_realtime() -> None:
    """Role workspace surfaces/hooks must use runtime.v2 push, not status snapshot refreshes."""

    findings: list[str] = []
    for path in FRONTEND_WORKSPACE_REALTIME_FILES:
        text = path.read_text(encoding="utf-8")
        for token in FRONTEND_WORKSPACE_FORBIDDEN_SNAPSHOT_REFRESH:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_task_market_consumers_use_event_wakeup_not_idle_polling() -> None:
    """Durable role consumers must block on task-market wake events when idle."""

    findings: list[str] = []
    for path in TASK_MARKET_EVENT_WAKE_FILES:
        text = path.read_text(encoding="utf-8")
        if "event_wakeup" not in text:
            findings.append(f"{path.relative_to(REPO_ROOT)} missing event_wakeup marker")
        for token in TASK_MARKET_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_agent_bus_mailbox_uses_event_wakeup_not_interval_polling() -> None:
    """Agent mailbox transport must wake on publish instead of interval sleeps."""

    findings: list[str] = []
    for path in EVENT_WAKE_AGENT_BUS_FILES:
        text = path.read_text(encoding="utf-8")
        for token in AGENT_BUS_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    bus_text = EVENT_WAKE_AGENT_BUS_FILES[0].read_text(encoding="utf-8")
    if "threading.Condition" not in bus_text or "call_soon_threadsafe" not in bus_text:
        findings.append("roles runtime bus_port.py must use condition/future wakeups")

    mailbox_text = EVENT_WAKE_AGENT_BUS_FILES[1].read_text(encoding="utf-8")
    if "block=True" not in mailbox_text:
        findings.append("BaseAgent mailbox consumer must block on event-woken bus receive")

    assert findings == []


def test_director_execution_loop_uses_event_wakeup_not_interval_polling() -> None:
    """Director execution loop must wake from task/worker events or convergence deadlines."""

    findings: list[str] = []
    for path in DIRECTOR_EXECUTION_EVENT_WAKE_FILES:
        text = path.read_text(encoding="utf-8")
        for token in DIRECTOR_EXECUTION_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

        for token in ("_loop_wakeup", "_wait_for_loop_signal", "_next_loop_deadline_delay"):
            if token not in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} missing {token!r}")

    assert findings == []


def test_role_worker_pool_uses_taskboard_ready_events_not_interval_polling() -> None:
    """Role worker pools must wake on direct submissions or TaskBoard ready events."""

    findings: list[str] = []
    for path in ROLE_WORKER_POOL_EVENT_WAKE_FILES:
        text = path.read_text(encoding="utf-8")
        for token in ROLE_WORKER_POOL_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    board_text = ROLE_WORKER_POOL_EVENT_WAKE_FILES[0].read_text(encoding="utf-8")
    for token in ("add_ready_listener", "wait_ready", "threading.Condition"):
        if token not in board_text:
            findings.append(f"task_board.py missing {token!r}")

    pool_text = ROLE_WORKER_POOL_EVENT_WAKE_FILES[1].read_text(encoding="utf-8")
    for token in ("_register_ready_listener", "_wake_condition", "_wake_event"):
        if token not in pool_text:
            findings.append(f"worker_pool.py missing {token!r}")

    assert findings == []


def test_pm_dispatch_worker_pool_uses_task_market_wake_events_not_interval_polling() -> None:
    """Inline PM dispatch workers must wait on TaskMarket wake events, not sleep loops."""

    findings: list[str] = []
    for path in PM_DISPATCH_WORKER_POOL_EVENT_WAKE_FILES:
        text = path.read_text(encoding="utf-8")
        for token in PM_DISPATCH_WORKER_POOL_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

        for token in ("get_task_market_work_event", "threading.Condition", "_run_wake_bridge", "_wait_for_pool_signal"):
            if token not in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} missing {token!r}")

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
