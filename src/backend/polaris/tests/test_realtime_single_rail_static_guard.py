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
    FRONTEND_SRC / "app" / "components" / "contextos" / "ContextOSWorkspace.tsx",
    FRONTEND_SRC / "app" / "hooks" / "useLiveTaskQueues.ts",
    FRONTEND_SRC / "app" / "hooks" / "useRuntime.ts",
    FRONTEND_SRC / "app" / "hooks" / "useRuntimeConnection.ts",
    FRONTEND_SRC / "hooks" / "useFactory.ts",
    FRONTEND_SRC / "hooks" / "useFactoryBench.ts",
    FRONTEND_SRC / "hooks" / "useProcessOperations.ts",
)
FRONTEND_LOG_VIEWER_FILE = FRONTEND_SRC / "app" / "components" / "LogViewer.tsx"
FRONTEND_CONTEXTOS_WORKSPACE_FILE = FRONTEND_SRC / "app" / "components" / "contextos" / "ContextOSWorkspace.tsx"
FRONTEND_CONTEXTOS_HELPER_FILES = (
    FRONTEND_SRC / "app" / "components" / "contextos" / "contextOSTelemetry.ts",
    FRONTEND_SRC / "app" / "components" / "contextos" / "contextOSData.ts",
    FRONTEND_SRC / "app" / "components" / "contextos" / "contextosViewModel.ts",
    FRONTEND_SRC / "app" / "components" / "contextos" / "useContextStoreStats.ts",
    FRONTEND_SRC / "app" / "components" / "contextos" / "contextosStoreStats.ts",
)
FRONTEND_SETTINGS_MODAL_FILE = FRONTEND_SRC / "app" / "components" / "SettingsModal.tsx"
FRONTEND_REALTIME_AUDIT_SPEC = (
    REPO_ROOT / "src" / "backend" / "polaris" / "tests" / "electron" / "realtime-nats-jetstream-workspaces.spec.ts"
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
WORKFLOW_CLIENT_EVENT_WAIT_FILES = (
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "workflow_runtime"
    / "internal"
    / "workflow_client.py",
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "workflow_runtime"
    / "internal"
    / "runtime_backend_adapter.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "kernelone" / "workflow" / "engine.py",
)
WORKFLOW_EMBEDDED_EVENT_WAIT_FILES = (
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "workflow_runtime"
    / "internal"
    / "embedded_api.py",
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "workflow_activity"
    / "internal"
    / "embedded_api.py",
    REPO_ROOT
    / "src"
    / "backend"
    / "polaris"
    / "cells"
    / "orchestration"
    / "workflow_runtime"
    / "internal"
    / "runtime_backend_adapter.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "kernelone" / "workflow" / "activity_runner.py",
)
JETSTREAM_BRIDGE_EVENT_DRAIN_FILES = (
    REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "http" / "routers" / "docs.py",
    REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "http" / "routers" / "interview.py",
)

RUNTIME_PROJECTION_FILES = (
    FRONTEND_SRC / "runtime" / "projection.ts",
    FRONTEND_SRC / "runtime" / "projectionAdapter.ts",
    FRONTEND_SRC / "runtime" / "guards.ts",
    FRONTEND_SRC / "runtime" / "selectors.ts",
    FRONTEND_SRC / "runtime" / "v2.ts",
)

RUNTIME_PROJECTION_FORBIDDEN_PATTERNS = (
    "apiFetch(",
    "fetch(",
    "EventSource",
    "text/event-stream",
    "setInterval(",
    "setTimeout(",
    "pollInterval",
    "usePolling",
    "useInterval",
    "/state/snapshot",
    "/v2/pm/status",
    "/v2/director/status",
    "/v2/health",
)

RUNTIME_PROJECTION_REQUIRED_WS_MARKERS = (
    "health",
    "metrics",
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
    "fetchRunStatus",
    "usePolling(",
    "useInterval(",
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

WORKFLOW_CLIENT_FORBIDDEN_INTERVAL_WAKEUPS = (
    "await asyncio.sleep(interval",
    "time.sleep(interval",
    "interval = max(0.2",
    "describe_workflow_sync(normalized_id",
)

WORKFLOW_EMBEDDED_FORBIDDEN_INTERVAL_WAKEUPS = (
    "runner.get_activity_status(activity_id",
    "runtime_engine.describe_workflow(child_id",
    "deadline = asyncio.get_running_loop().time() + timeout_seconds",
    "await asyncio.sleep(0.05",
)

JETSTREAM_BRIDGE_FORBIDDEN_INTERVAL_WAKEUPS = (
    "wait_for(queue.get(), timeout=",
    "await asyncio.sleep(",
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
    "Nats-JetStream",
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


def test_log_viewer_uses_runtime_transport_not_file_read_tail_polling() -> None:
    """Runtime log surfaces must subscribe to runtime.v2, not tail log files through HTTP."""

    text = FRONTEND_LOG_VIEWER_FILE.read_text(encoding="utf-8")
    findings: list[str] = []
    for token in ("/files/read", "tail_lines=", "tailLines=400"):
        if token in text:
            findings.append(f"{FRONTEND_LOG_VIEWER_FILE.relative_to(REPO_ROOT)} contains {token!r}")
    for token in ("useRuntimeTransport", "subscribeChannels", "registerMessageHandler", "tailLines: 400"):
        if token not in text:
            findings.append(f"{FRONTEND_LOG_VIEWER_FILE.relative_to(REPO_ROOT)} missing {token!r}")

    assert findings == []


CONTEXTOS_FORBIDDEN_DATA_REFRESH_PATTERNS = (
    "setInterval(",
    "setTimeout(",
    "useInterval",
    "usePolling",
    "pollInterval",
    "EventSource",
    "text/event-stream",
    "apiFetchFresh",
    "fetchRunStatus",
    "/state/snapshot",
    "/v2/pm/status",
    "/v2/director/status",
    "apiFetch('/runtime",
    'apiFetch("/runtime',
    "tail_lines=",
    "/files/read",
)

CONTEXTOS_REQUIRED_REALTIME_MARKERS = (
    "buildTelemetryFromStream",
    "llmStreamEvents",
    "executionLogs",
    "processStreamEvents",
)


def test_contextos_workspace_uses_runtime_transport_not_http_polling() -> None:
    """ContextOS realtime view must derive data from WebSocket push, not HTTP polling or SSE."""

    text = FRONTEND_CONTEXTOS_WORKSPACE_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    for token in CONTEXTOS_FORBIDDEN_DATA_REFRESH_PATTERNS:
        if token in text:
            findings.append(f"{FRONTEND_CONTEXTOS_WORKSPACE_FILE.relative_to(REPO_ROOT)} contains {token!r}")

    for token in CONTEXTOS_REQUIRED_REALTIME_MARKERS:
        if token not in text:
            findings.append(f"{FRONTEND_CONTEXTOS_WORKSPACE_FILE.relative_to(REPO_ROOT)} missing {token!r}")

    assert findings == []


def test_contextos_workspace_does_not_import_use_runtime_transport_directly() -> None:
    """ContextOS must receive realtime data via props, not subscribe independently."""

    text = FRONTEND_CONTEXTOS_WORKSPACE_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    forbidden_imports = (
        "useRuntimeTransport",
        "useRuntime(",
        "useRuntimeConnection",
        "runtimeSocketManager",
    )

    for token in forbidden_imports:
        if token in text:
            findings.append(
                f"{FRONTEND_CONTEXTOS_WORKSPACE_FILE.relative_to(REPO_ROOT)} directly imports {token!r}; "
                "ContextOS should receive data via props from parent workspace"
            )

    assert findings == []


CONTEXTOS_HELPER_FORBIDDEN_PATTERNS = (
    "setInterval(",
    "setTimeout(",
    "useInterval",
    "usePolling",
    "pollInterval",
    "EventSource",
    "text/event-stream",
    "apiFetchFresh",
    "fetchRunStatus",
    "/files/read",
    "tail_lines=",
)

LLM_COMPONENTS_DIR = FRONTEND_SRC / "app" / "components" / "llm"

LLM_COMPONENTS_FORBIDDEN_PATTERNS = (
    "EventSource",
    "text/event-stream",
    "useWebSocketWithFallback",
    "fallbackInterval",
    "maxFallbackAttempts",
    "longPolling",
    "long-poll",
    "fetchRunStatus",
    "usePolling(",
    "useInterval(",
)

LLM_COMPONENTS_REQUIRED_TRANSPORT_MARKERS = (
    "useRuntimeTransport",
    "runtimeSocketManager",
)

LLM_VISUAL_HOOK_FILE = FRONTEND_SRC / "app" / "components" / "llm" / "visual" / "hooks" / "useVisualLLMConfig.ts"

LLM_INTERVIEW_STREAM_FILE = FRONTEND_SRC / "app" / "components" / "llm" / "interview" / "useInterviewStream.ts"

LLM_TEST_STREAM_FILE = FRONTEND_SRC / "app" / "components" / "llm" / "test" / "streamingTest.ts"


def test_contextos_helper_files_have_no_polling_or_sse() -> None:
    """ContextOS helper modules must not introduce HTTP polling, SSE, or timer-based data refresh."""

    findings: list[str] = []
    for path in FRONTEND_CONTEXTOS_HELPER_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in CONTEXTOS_HELPER_FORBIDDEN_PATTERNS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_llm_components_directory_has_no_sse_or_polling() -> None:
    """LLM metrics/UI components must use WS transport, not SSE or polling."""

    findings: list[str] = []
    for path in LLM_COMPONENTS_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if "__tests__" in path.parts or ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for token in LLM_COMPONENTS_FORBIDDEN_PATTERNS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_llm_interview_stream_uses_runtime_transport() -> None:
    """LLM interview stream must consume data via runtime transport, not polling."""

    if not LLM_INTERVIEW_STREAM_FILE.exists():
        return

    text = LLM_INTERVIEW_STREAM_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    has_transport = any(m in text for m in LLM_COMPONENTS_REQUIRED_TRANSPORT_MARKERS)
    if not has_transport:
        findings.append(
            f"{LLM_INTERVIEW_STREAM_FILE.relative_to(REPO_ROOT)} missing runtime transport "
            f"(expected one of: {LLM_COMPONENTS_REQUIRED_TRANSPORT_MARKERS})"
        )

    for token in LLM_COMPONENTS_FORBIDDEN_PATTERNS:
        if token in text:
            findings.append(f"{LLM_INTERVIEW_STREAM_FILE.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_llm_test_stream_uses_runtime_transport() -> None:
    """LLM test stream must use runtimeSocketManager, not custom polling."""

    if not LLM_TEST_STREAM_FILE.exists():
        return

    text = LLM_TEST_STREAM_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    if "runtimeSocketManager" not in text:
        findings.append(f"{LLM_TEST_STREAM_FILE.relative_to(REPO_ROOT)} missing 'runtimeSocketManager'")

    for token in LLM_COMPONENTS_FORBIDDEN_PATTERNS:
        if token in text:
            findings.append(f"{LLM_TEST_STREAM_FILE.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_llm_visual_config_hook_uses_one_shot_fetch_not_polling() -> None:
    """LLM visual config hook must fetch runtime status once on mount, not poll."""

    if not LLM_VISUAL_HOOK_FILE.exists():
        return

    text = LLM_VISUAL_HOOK_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    if "apiFetch('/v2/llm/runtime-status')" not in text and 'apiFetch("/v2/llm/runtime-status")' not in text:
        findings.append(f"{LLM_VISUAL_HOOK_FILE.relative_to(REPO_ROOT)} missing one-shot runtime-status fetch")

    for token in ("setInterval(", "setTimeout(", "pollInterval", "usePolling"):
        if token in text:
            findings.append(f"{LLM_VISUAL_HOOK_FILE.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_contextos_workspace_llm_metrics_derive_from_ws_push() -> None:
    """ContextOS LLM metrics (calls, tokens, latency) must derive from WS push data, not HTTP polling."""

    text = FRONTEND_CONTEXTOS_WORKSPACE_FILE.read_text(encoding="utf-8")
    findings: list[str] = []

    for token in (
        "model.calls",
        "model.totalTokens",
        "model.realLatencyMs",
        "llmRuntimeState",
    ):
        if token not in text:
            findings.append(f"{FRONTEND_CONTEXTOS_WORKSPACE_FILE.relative_to(REPO_ROOT)} missing {token!r}")

    ws_markers = (
        "WebSocket /v2/ws/runtime",
        "Nats-JetStream",
        "runtime.v2",
    )
    has_ws_marker = any(m in text for m in ws_markers)
    if not has_ws_marker:
        findings.append(
            f"{FRONTEND_CONTEXTOS_WORKSPACE_FILE.relative_to(REPO_ROOT)} missing WS transport marker "
            "(WebSocket /v2/ws/runtime, Nats-JetStream, or runtime.v2)"
        )

    assert findings == []


def test_llm_runtime_overlay_data_from_props_not_polling() -> None:
    """LlmRuntimeOverlay must receive data via props, not fetch or timer."""

    llm_overlay = FRONTEND_SRC / "app" / "components" / "LlmRuntimeOverlay.tsx"
    if not llm_overlay.exists():
        return

    text = llm_overlay.read_text(encoding="utf-8")
    findings: list[str] = []

    for token in ("apiFetch(", "fetch(", "EventSource", "pollInterval"):
        if token in text:
            findings.append(f"{llm_overlay.relative_to(REPO_ROOT)} contains {token!r}")

    for token in ("setInterval(",):
        if token not in text:
            findings.append(f"{llm_overlay.relative_to(REPO_ROOT)} missing UI timer (setInterval)")

    assert findings == []


def test_frontend_use_usage_stats_has_no_polling() -> None:
    """useUsageStats must derive from WS push, not file polling or HTTP polling."""

    usage_file = FRONTEND_SRC / "app" / "hooks" / "useUsageStats.ts"
    if not usage_file.exists():
        return

    text = usage_file.read_text(encoding="utf-8")
    findings: list[str] = []

    for token in FRONTEND_FORBIDDEN:
        if token in text:
            findings.append(f"{usage_file.relative_to(REPO_ROOT)} contains {token!r}")

    for token in ("setInterval(", "setTimeout("):
        if token in text:
            findings.append(f"{usage_file.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []


def test_settings_modal_does_not_refresh_llm_status_while_closed_or_off_tab() -> None:
    """Settings modal must not create hidden /v2/llm/status refreshes during workspace navigation."""

    text = FRONTEND_SETTINGS_MODAL_FILE.read_text(encoding="utf-8")
    findings: list[str] = []
    forbidden_tokens = (
        "if (!isOpen) {\n      loadLLMStatus",
        "if (!isOpen) {\r\n      loadLLMStatus",
        "if (!isOpen) return;\n    loadLLMConfig",
    )
    for token in forbidden_tokens:
        if token in text:
            findings.append(f"{FRONTEND_SETTINGS_MODAL_FILE.relative_to(REPO_ROOT)} contains {token!r}")

    for token in ("if (!isOpen || activeTab !== 'llm') return;", "}, [activeTab, isOpen]);"):
        if token not in text:
            findings.append(f"{FRONTEND_SETTINGS_MODAL_FILE.relative_to(REPO_ROOT)} missing {token!r}")

    assert findings == []


def test_workspace_realtime_playwright_audit_forbids_sse_file_and_status_polling() -> None:
    """The cross-workspace E2E audit must keep checking the no-SSE/no-polling contract."""

    text = FRONTEND_REALTIME_AUDIT_SPEC.read_text(encoding="utf-8")
    findings: list[str] = []
    for token in (
        "text/event-stream",
        'path === "/files/read"',
        'path === "/v2/llm/status"',
        "repeatedPolling",
        "runtime.v2 WebSocket should be used",
    ):
        if token not in text:
            findings.append(f"{FRONTEND_REALTIME_AUDIT_SPEC.relative_to(REPO_ROOT)} missing {token!r}")

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


def test_workflow_client_wait_uses_runtime_task_completion_not_status_polling() -> None:
    """Workflow wait APIs must wait on runtime task completion, not describe/sleep loops."""

    findings: list[str] = []
    for path in WORKFLOW_CLIENT_EVENT_WAIT_FILES:
        text = path.read_text(encoding="utf-8")
        if path.name == "workflow_client.py":
            for token in WORKFLOW_CLIENT_FORBIDDEN_INTERVAL_WAKEUPS:
                if token in text:
                    findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")
        if "wait_workflow_completion" not in text:
            findings.append(f"{path.relative_to(REPO_ROOT)} missing wait_workflow_completion")

    assert findings == []


def test_embedded_workflow_waits_use_event_completion_not_status_polling() -> None:
    """Embedded activity/child-workflow waits must block on runtime completion events."""

    findings: list[str] = []
    for path in WORKFLOW_EMBEDDED_EVENT_WAIT_FILES:
        text = path.read_text(encoding="utf-8")
        for token in WORKFLOW_EMBEDDED_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    for path in WORKFLOW_EMBEDDED_EVENT_WAIT_FILES[:2]:
        text = path.read_text(encoding="utf-8")
        for token in ("_wait_child_workflow_completion", "wait_workflow_completion", "wait_activity_status"):
            if token not in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} missing {token!r}")

    adapter_text = WORKFLOW_EMBEDDED_EVENT_WAIT_FILES[2].read_text(encoding="utf-8")
    for token in ("wait_workflow_completion", "wait_activity_status"):
        if token not in adapter_text:
            findings.append(f"runtime_backend_adapter.py missing {token!r}")

    runner_text = WORKFLOW_EMBEDDED_EVENT_WAIT_FILES[3].read_text(encoding="utf-8")
    for token in ("wait_activity_status", "asyncio.Condition", "_notify_status_change"):
        if token not in runner_text:
            findings.append(f"activity_runner.py missing {token!r}")

    assert findings == []


def test_http_jetstream_bridges_drain_queues_without_interval_polling() -> None:
    """HTTP-triggered JetStream bridge tasks must wait on producer/queue events."""

    findings: list[str] = []
    for path in JETSTREAM_BRIDGE_EVENT_DRAIN_FILES:
        text = path.read_text(encoding="utf-8")
        for token in JETSTREAM_BRIDGE_FORBIDDEN_INTERVAL_WAKEUPS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")
        for token in ("asyncio.wait", "FIRST_COMPLETED", "queue.get_nowait"):
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


def test_runtime_projection_files_have_no_polling_for_health_or_metrics() -> None:
    """Runtime projection/guards/selectors must derive health/metrics from WS push, not polling."""

    findings: list[str] = []
    combined_text = ""
    for path in RUNTIME_PROJECTION_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        combined_text += text
        for token in RUNTIME_PROJECTION_FORBIDDEN_PATTERNS:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    for marker in RUNTIME_PROJECTION_REQUIRED_WS_MARKERS:
        if marker not in combined_text:
            findings.append(f"Runtime projection layer missing WS-derived marker {marker!r}")

    assert findings == []


def test_runtime_projection_adapter_derives_director_metrics_from_ws_push() -> None:
    """projectionAdapter.ts must derive director metrics from WS message, not HTTP fetch."""

    text = FRONTEND_SRC / "runtime" / "projectionAdapter.ts"
    if not text.exists():
        return
    content = text.read_text(encoding="utf-8")
    findings: list[str] = []

    for marker in (
        "DirectorServiceMetrics",
        "metricValue",
        "normalizeHealthStatus",
        "engine_status",
        "health",
    ):
        if marker not in content:
            findings.append(f"projectionAdapter.ts missing WS-derived {marker!r}")

    for forbidden in ("apiFetch(", "fetch(", "setInterval(", "pollInterval"):
        if forbidden in content:
            findings.append(f"projectionAdapter.ts contains polling pattern {forbidden!r}")

    assert findings == []


def test_runtime_guards_check_health_without_polling() -> None:
    """guards.ts must check LanceDB/health status from WS state, not HTTP polling."""

    guards_file = FRONTEND_SRC / "runtime" / "guards.ts"
    if not guards_file.exists():
        return
    content = guards_file.read_text(encoding="utf-8")
    findings: list[str] = []

    for marker in ("health", "ready", "healthy"):
        if marker not in content:
            findings.append(f"guards.ts missing health marker {marker!r}")

    for forbidden in ("apiFetch(", "fetch(", "setInterval(", "EventSource"):
        if forbidden in content:
            findings.append(f"guards.ts contains polling pattern {forbidden!r}")

    assert findings == []


def test_runtime_event_v2_schema_includes_metrics_field() -> None:
    """v2.ts RuntimeEventV2 schema must include metrics field for WS-pushed health/metrics data."""

    v2_file = FRONTEND_SRC / "runtime" / "v2.ts"
    if not v2_file.exists():
        return
    content = v2_file.read_text(encoding="utf-8")
    findings: list[str] = []

    if "metrics: z.record" not in content:
        findings.append("v2.ts missing 'metrics: z.record' in RuntimeEventV2 schema")

    for forbidden in ("apiFetch(", "fetch(", "setInterval(", "EventSource"):
        if forbidden in content:
            findings.append(f"v2.ts contains polling pattern {forbidden!r}")

    assert findings == []
