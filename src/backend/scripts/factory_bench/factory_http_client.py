from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any, Callable

import websockets
from websockets.exceptions import WebSocketException

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER_S = 5.0
FACTORY_EVENT_TAIL = 80
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "canceled"}
# When runtime.v2 misses a late terminal event (subscription race / NATS half-close),
# poll HTTP so isolated true-runs do not sit for the full timeout after the run is already failed.
HTTP_TERMINAL_POLL_INTERVAL_S = 5.0
# complete_run/settle can hold the backend run-lock for tens of seconds; short GETs time out
# (R63 logged 13× "backend GET failed: timed out") even though durable run.json is already terminal.
# R163: Director multi-task load blocked the event loop for >30s stretches; terminal polls and
# audit-bundle GETs need a longer per-attempt budget plus transport retries.
HTTP_TERMINAL_POLL_TIMEOUT_S = 60.0
HTTP_OBSERVATION_MAX_RETRIES = 3
HTTP_OBSERVATION_RETRY_BACKOFF_BASE_S = 1.0
HTTP_OBSERVATION_RETRY_BACKOFF_CAP_S = 8.0
# R153: long Director multi-task stages can block the backend event loop long enough that the
# default websockets ping_timeout (~20s) raises 1011 keepalive ping timeout. Observation
# failure must not become an execution kill: reconnect until the wall-clock deadline, and
# tolerate slower pings under load.
EVENT_WAIT_WS_PING_INTERVAL_S = 20.0
EVENT_WAIT_WS_PING_TIMEOUT_S = 120.0
EVENT_WAIT_WS_CLOSE_TIMEOUT_S = 5.0
EVENT_WAIT_RECONNECT_BACKOFF_BASE_S = 1.0
EVENT_WAIT_RECONNECT_BACKOFF_CAP_S = 15.0


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    if exc.code != 429:
        return None
    raw = None
    headers = getattr(exc, "headers", None)
    if headers is not None:
        raw = headers.get("Retry-After")
    try:
        delay = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        delay = 1.0
    return min(max(delay, 0.0), MAX_RETRY_AFTER_S)


def _http_error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        raw = ""
    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    return {
        "status": int(getattr(exc, "code", 0) or 0),
        "reason": str(getattr(exc, "reason", "") or ""),
        "body": raw,
        "json": parsed if isinstance(parsed, dict) else None,
    }


def _append_query_params(url: str, params: Mapping[str, str]) -> str:
    clean_params = [(key, value) for key, value in params.items() if value]
    if not clean_params:
        return url
    parsed = urllib.parse.urlsplit(url)
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(
        [*existing, *clean_params],
        quote_via=urllib.parse.quote,
    )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _runtime_ws_url(backend_url: str, *, token: str = "", workspace: str = "") -> str:
    parsed = urllib.parse.urlparse(backend_url.rstrip("/"))
    if not parsed.netloc:
        raise ValueError(f"invalid backend url: {backend_url!r}")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query: dict[str, str] = {"protocol": "runtime.v2"}
    if token:
        query["token"] = token
    if workspace:
        query["workspace"] = workspace
    query_text = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return f"{scheme}://{parsed.netloc}/v2/ws/runtime?{query_text}"


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _factory_event_payload(message: Mapping[str, Any], run_id: str) -> tuple[int, dict[str, Any]] | None:
    if str(message.get("type") or "").strip().upper() != "EVENT":
        return None
    event = _as_mapping(message.get("event"))
    channel = str(event.get("channel") or "").strip()
    if channel not in {"event.factory", f"event.factory:{run_id}"}:
        return None
    payload = _as_mapping(event.get("payload"))
    if not payload:
        return None
    event_run_id = str(event.get("run_id") or payload.get("run_id") or "").strip()
    if event_run_id and event_run_id != run_id:
        return None
    payload.setdefault("run_id", run_id)
    try:
        cursor = int(message.get("cursor") or event.get("cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    return cursor, payload


def _status_from_factory_event(
    run_id: str,
    payload: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = dict(previous or {})
    status["run_id"] = run_id
    event_type = str(payload.get("type") or payload.get("kind") or "").strip().lower()
    status["event_type"] = event_type
    status["event_payload"] = dict(payload)
    stage = str(payload.get("stage") or payload.get("phase") or "").strip()
    if stage:
        status["phase"] = stage
        status["current_stage"] = stage

    result = _as_mapping(payload.get("result"))
    result_status = str(result.get("status") or "").strip().lower()

    if event_type in {"started", "stage_started", "stage_heartbeat", "resumed", "metadata_updated"}:
        status["status"] = "running"
    elif event_type == "paused":
        status["status"] = "paused"
    elif event_type == "stage_completed":
        if result_status in {"failed", "cancelled", "canceled"}:
            status["status"] = "cancelled" if result_status in {"cancelled", "canceled"} else "failed"
        elif str(status.get("status") or "").strip().lower() not in TERMINAL_RUN_STATUSES:
            status["status"] = "running"
    elif event_type in {"completed", "failed", "cancelled", "canceled"}:
        status["status"] = "cancelled" if event_type == "canceled" else event_type

    if "status" not in status:
        status["status"] = "running"
    return status


async def _ack_runtime_cursor(ws: Any, cursor: int) -> None:
    if cursor <= 0:
        return
    await ws.send(
        json.dumps(
            {
                "type": "ACK",
                "protocol": "runtime.v2",
                "cursor": cursor,
            },
            ensure_ascii=False,
        )
    )


async def _subscribe_factory_events(ws: Any, *, run_id: str, workspace: str) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "SUBSCRIBE",
                "protocol": "runtime.v2",
                "client_id": f"factory-bench-{uuid.uuid4().hex[:10]}",
                "channels": ["event.factory", f"event.factory:{run_id}"],
                "cursor": 0,
                "tail": FACTORY_EVENT_TAIL,
                "workspace": workspace,
            },
            ensure_ascii=False,
        )
    )
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        try:
            message = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(message, Mapping):
            continue
        msg_type = str(message.get("type") or "").strip().upper()
        if msg_type == "SUBSCRIBED":
            return
        if msg_type == "ERROR":
            payload = _as_mapping(message.get("payload"))
            raise RuntimeError(str(payload.get("error") or payload.get("code") or "runtime.v2 subscribe failed"))


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
    return_errors: bool = False,
) -> dict[str, Any] | None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            break
        except urllib.error.HTTPError as exc:
            delay = _retry_after_seconds(exc)
            if delay is not None and attempt < attempts - 1:
                print(
                    f"[factory-bench] backend POST rate-limited: {url}; retrying in {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            error_payload = _http_error_payload(exc)
            print(
                f"[factory-bench] backend POST failed: {url}: {exc}; body={error_payload.get('body') or ''}",
                file=sys.stderr,
                flush=True,
            )
            if return_errors:
                return {"_http_error": error_payload}
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"[factory-bench] backend POST failed: {url}: {exc}", file=sys.stderr, flush=True)
            if return_errors:
                return {
                    "_http_error": {
                        "status": 0,
                        "reason": str(exc),
                        "body": "",
                        "json": None,
                        "exception": type(exc).__name__,
                        "url": url,
                    }
                }
            return None
    else:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _http_get_json(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any] | None:
    """GET JSON with retries for rate-limit and transport/timeouts under load.

    R163 residual: under long Director stages the isolated backend event loop
    can stall longer than a single GET timeout. Returning None on the first
    ``timed out`` made observation look dead while the run was still healthy.
    Retries stay within the caller's wall budget (caller chooses timeout_s and
    max_retries); only the final failed attempt logs a hard failure.
    """

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    attempts = max(1, max_retries + 1)
    raw = ""
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            break
        except urllib.error.HTTPError as exc:
            last_exc = exc
            delay = _retry_after_seconds(exc)
            if delay is not None and attempt < attempts - 1:
                print(
                    f"[factory-bench] backend GET rate-limited: {url}; retrying in {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            # Non-429 HTTP errors are not retried (auth/not-found are fail-closed).
            if attempt < attempts - 1 and int(getattr(exc, "code", 0) or 0) >= 500:
                delay = min(
                    HTTP_OBSERVATION_RETRY_BACKOFF_CAP_S,
                    HTTP_OBSERVATION_RETRY_BACKOFF_BASE_S * (2**attempt),
                )
                print(
                    f"[factory-bench] backend GET HTTP {exc.code}: {url}; "
                    f"retrying in {delay:.1f}s (attempt {attempt + 1}/{attempts})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(f"[factory-bench] backend GET failed: {url}: {exc}", file=sys.stderr, flush=True)
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = min(
                    HTTP_OBSERVATION_RETRY_BACKOFF_CAP_S,
                    HTTP_OBSERVATION_RETRY_BACKOFF_BASE_S * (2**attempt),
                )
                print(
                    f"[factory-bench] backend GET transport error: {url}: {exc}; "
                    f"retrying in {delay:.1f}s (attempt {attempt + 1}/{attempts})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(f"[factory-bench] backend GET failed: {url}: {exc}", file=sys.stderr, flush=True)
            return None
    else:
        if last_exc is not None:
            print(f"[factory-bench] backend GET failed: {url}: {last_exc}", file=sys.stderr, flush=True)
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def start_factory_run(
    backend_url: str,
    payload: dict[str, Any],
    token: str = "",
) -> dict[str, Any] | None:
    return _http_post_json(f"{backend_url}/v2/factory/runs", payload, token=token, return_errors=True)


def get_run_status(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int | None = None,
) -> dict[str, Any] | None:
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}", {"workspace": workspace})
    retries = DEFAULT_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    return _http_get_json(url, token=token, timeout_s=timeout_s, max_retries=retries)


def cancel_factory_run(
    backend_url: str,
    run_id: str,
    *,
    reason: str = "",
    token: str = "",
    workspace: str = "",
    return_errors: bool = False,
) -> dict[str, Any] | None:
    payload = {
        "action": "cancel",
        "reason": reason or "factory-bench cancelled run",
    }
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/control", {"workspace": workspace})
    return _http_post_json(url, payload, token=token, return_errors=return_errors)


def get_audit_bundle(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
    *,
    timeout_s: float = HTTP_TERMINAL_POLL_TIMEOUT_S,
    max_retries: int = HTTP_OBSERVATION_MAX_RETRIES,
) -> dict[str, Any] | None:
    """Fetch audit-bundle with long timeout + transport retries (R163).

    Post-terminal audit collection must not fail-closed on a single short GET
    while the backend is still draining Director settle work.
    """

    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/audit-bundle", {"workspace": workspace})
    return _http_get_json(
        url,
        token=token,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )


def get_run_artifacts(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
) -> dict[str, Any] | None:
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/artifacts", {"workspace": workspace})
    return _http_get_json(url, token=token)


def _coerce_terminal_run_status(status: Mapping[str, Any], *, run_id: str) -> dict[str, Any] | None:
    """Return a terminal status snapshot, or None when still non-terminal."""

    payload = dict(status)
    payload["run_id"] = str(payload.get("run_id") or run_id).strip() or run_id
    current = str(payload.get("status") or "").strip().lower()
    if current not in TERMINAL_RUN_STATUSES:
        return None
    if current == "canceled":
        payload["status"] = "cancelled"
    return payload


def _durable_run_status_snapshot(
    run_id: str,
    *,
    workspace: str = "",
    latest_status: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read durable factory run.json from KernelOne storage (no live HTTP).

    Used when HTTP status polls time out because complete_run holds the run lock
    but the durable record has already been written terminal.
    """

    workspace_path = str(workspace or "").strip()
    if not workspace_path:
        return None
    try:
        from pathlib import Path

        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(workspace_path)
        run_path = Path(roots.runtime_root) / "factory" / str(run_id).strip() / "run.json"
        if not run_path.is_file():
            return None
        raw = run_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    status_token = str(payload.get("status") or "").strip().lower()
    merged: dict[str, Any] = {
        **dict(latest_status or {}),
        "run_id": run_id,
        "status": status_token,
        "phase": status_token or str((latest_status or {}).get("phase") or ""),
        "completed_at": payload.get("completed_at"),
        "current_stage": (payload.get("metadata") or {}).get("current_stage")
        if isinstance(payload.get("metadata"), Mapping)
        else None,
    }
    terminal = _coerce_terminal_run_status(merged, run_id=run_id)
    if terminal is None:
        return None
    terminal["_terminal_source"] = "durable_run_json"
    return terminal


def _http_terminal_status_snapshot(
    backend_url: str,
    run_id: str,
    *,
    token: str = "",
    workspace: str = "",
    latest_status: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Best-effort terminal snapshot when runtime.v2 events are missed."""

    terminal, _progress = _http_observation_status_snapshot(
        backend_url,
        run_id,
        token=token,
        workspace=workspace,
        latest_status=latest_status,
    )
    return terminal


def _http_observation_status_snapshot(
    backend_url: str,
    run_id: str,
    *,
    token: str = "",
    workspace: str = "",
    latest_status: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """One HTTP (or durable) observation poll under Director load.

    Returns ``(terminal_or_none, progress_or_none)``. Preference:
    1. HTTP GET with extended timeout + transport retries.
    2. Durable run.json terminal (when HTTP is blocked during settle).

    R163: non-terminal HTTP answers still return as progress so the bench
    chain log advances phase instead of looking frozen during load storms.
    """

    snapshot = get_run_status(
        backend_url,
        run_id,
        token=token,
        workspace=workspace,
        timeout_s=HTTP_TERMINAL_POLL_TIMEOUT_S,
        max_retries=HTTP_OBSERVATION_MAX_RETRIES,
    )
    if isinstance(snapshot, Mapping):
        merged: dict[str, Any] = _merge_observation_progress(
            latest_status or {},
            run_id=run_id,
            snapshot=snapshot,
        )
        terminal = _coerce_terminal_run_status(merged, run_id=run_id)
        if terminal is not None:
            terminal["_terminal_source"] = "http_status_poll"
            return terminal, merged
        return None, merged
    durable = _durable_run_status_snapshot(
        run_id,
        workspace=workspace,
        latest_status=latest_status,
    )
    return durable, None


def _merge_observation_progress(
    latest_status: Mapping[str, Any],
    *,
    run_id: str,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge a non-terminal HTTP/durable snapshot into the observation cursor."""

    if not isinstance(snapshot, Mapping):
        return dict(latest_status)
    merged = {**dict(latest_status), **dict(snapshot), "run_id": run_id}
    # Never let an empty status clobber a known running/terminal cursor.
    if not str(merged.get("status") or "").strip():
        merged["status"] = str(latest_status.get("status") or "running")
    return merged


def wait_run_until_terminal(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    initial_status: Mapping[str, Any] | None = None,
    return_diagnostics: bool = False,
) -> dict[str, Any] | None:
    try:
        return asyncio.run(
            _wait_run_until_terminal_async(
                backend_url,
                run_id,
                token=token,
                workspace=workspace,
                timeout_s=timeout_s,
                on_status=on_status,
                initial_status=initial_status,
                return_diagnostics=return_diagnostics,
            )
        )
    except RuntimeError as exc:
        print(f"[factory-bench] event wait failed: {run_id}: {exc}", file=sys.stderr, flush=True)
        http_terminal = _http_terminal_status_snapshot(
            backend_url,
            run_id,
            token=token,
            workspace=workspace,
            latest_status=dict(initial_status or {}),
        )
        if http_terminal is not None:
            if on_status is not None:
                on_status(http_terminal)
            return http_terminal
        if return_diagnostics:
            return _event_wait_diagnostic_status(
                run_id,
                dict(initial_status or {}),
                kind="runtime_error",
                message=str(exc),
                backend_url=backend_url,
                workspace=workspace,
            )
        return None


async def _wait_run_until_terminal_async(
    backend_url: str,
    run_id: str,
    *,
    token: str = "",
    workspace: str = "",
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    initial_status: Mapping[str, Any] | None = None,
    return_diagnostics: bool = False,
) -> dict[str, Any] | None:
    """Wait for a factory run terminal status via runtime.v2, with HTTP poll fallback.

    R153 invariant: observation-path failures (WS keepalive ping timeout, half-close,
    transient disconnect) must reconnect until the wall-clock deadline. Only after the
    deadline is exhausted may this return a non-terminal diagnostic that causes the
    bench runner to cancel the backend run. Cancelling a healthy multi-task Director
    stage on a single keepalive drop produces ``factory_physical_attempt_authority_closed``
    and false ``tool_lifecycle_failed`` residuals.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_s)
    latest_status: dict[str, Any] = {"run_id": run_id, **dict(initial_status or {})}
    latest_status.setdefault("status", "running")
    if on_status is not None:
        on_status(latest_status)

    initial_terminal = _coerce_terminal_run_status(latest_status, run_id=run_id)
    if initial_terminal is not None:
        return initial_terminal

    # Catch races where the run finished before the WS subscription is live.
    http_terminal = await asyncio.to_thread(
        _http_terminal_status_snapshot,
        backend_url,
        run_id,
        token=token,
        workspace=workspace,
        latest_status=latest_status,
    )
    if http_terminal is not None:
        if on_status is not None:
            on_status(http_terminal)
        return http_terminal

    ws_url = _runtime_ws_url(backend_url, token=token, workspace=workspace)
    last_connection_error = ""
    reconnect_attempt = 0

    async def _poll_http_terminal() -> dict[str, Any] | None:
        nonlocal latest_status
        terminal, progress = await asyncio.to_thread(
            _http_observation_status_snapshot,
            backend_url,
            run_id,
            token=token,
            workspace=workspace,
            latest_status=latest_status,
        )
        if terminal is not None:
            latest_status = terminal
            if on_status is not None:
                on_status(terminal)
            return terminal
        if isinstance(progress, Mapping):
            updated = _merge_observation_progress(latest_status, run_id=run_id, snapshot=progress)
            if updated != latest_status:
                latest_status = updated
                if on_status is not None:
                    on_status(latest_status)
        return None

    def _timeout_diagnostic(*, kind: str, message: str) -> dict[str, Any] | None:
        print(f"[factory-bench] event wait timeout: {run_id}", file=sys.stderr, flush=True)
        if not return_diagnostics:
            return None
        return _event_wait_diagnostic_status(
            run_id,
            latest_status,
            kind=kind,
            message=message,
            backend_url=backend_url,
            workspace=workspace,
        )

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            http_terminal = await _poll_http_terminal()
            if http_terminal is not None:
                return http_terminal
            if last_connection_error:
                return _timeout_diagnostic(
                    kind="runtime_v2_connection_failed",
                    message=(f"runtime.v2 connection failed until deadline ({timeout_s}s): {last_connection_error}"),
                )
            return _timeout_diagnostic(
                kind="timeout",
                message=f"runtime.v2 did not deliver terminal event within {timeout_s}s",
            )

        try:
            async with websockets.connect(
                ws_url,
                ping_interval=EVENT_WAIT_WS_PING_INTERVAL_S,
                ping_timeout=EVENT_WAIT_WS_PING_TIMEOUT_S,
                close_timeout=EVENT_WAIT_WS_CLOSE_TIMEOUT_S,
            ) as ws:
                # Successful open resets reconnect backoff so intermittent drops
                # do not permanently stretch the sleep schedule.
                reconnect_attempt = 0
                last_connection_error = ""
                await _subscribe_factory_events(ws, run_id=run_id, workspace=workspace)
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        http_terminal = await _poll_http_terminal()
                        if http_terminal is not None:
                            return http_terminal
                        return _timeout_diagnostic(
                            kind="timeout",
                            message=f"runtime.v2 did not deliver terminal event within {timeout_s}s",
                        )
                    recv_timeout = min(HTTP_TERMINAL_POLL_INTERVAL_S, remaining)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                    except asyncio.TimeoutError:
                        http_terminal = await _poll_http_terminal()
                        if http_terminal is not None:
                            return http_terminal
                        continue
                    try:
                        message = json.loads(str(raw))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(message, Mapping):
                        continue
                    event_payload = _factory_event_payload(message, run_id)
                    if event_payload is None:
                        continue
                    cursor, payload = event_payload
                    await _ack_runtime_cursor(ws, cursor)
                    latest_status = _status_from_factory_event(run_id, payload, latest_status)
                    if on_status is not None:
                        on_status(latest_status)
                    terminal = _coerce_terminal_run_status(latest_status, run_id=run_id)
                    if terminal is not None:
                        return terminal
        except asyncio.TimeoutError:
            http_terminal = await _poll_http_terminal()
            if http_terminal is not None:
                return http_terminal
            remaining = deadline - loop.time()
            if remaining <= 0:
                return _timeout_diagnostic(
                    kind="timeout",
                    message=f"runtime.v2 did not deliver terminal event within {timeout_s}s",
                )
            # Treat unexpected outer TimeoutError like a soft disconnect and reconnect.
            last_connection_error = "asyncio.TimeoutError during runtime.v2 event wait"
            print(
                f"[factory-bench] runtime.v2 event wait timed out mid-stream: {run_id}; "
                f"reconnecting ({remaining:.1f}s budget left)",
                file=sys.stderr,
                flush=True,
            )
        except (OSError, ValueError, RuntimeError, WebSocketException) as exc:
            last_connection_error = str(exc)
            http_terminal = await _poll_http_terminal()
            if http_terminal is not None:
                return http_terminal
            remaining = deadline - loop.time()
            if remaining <= 0:
                print(
                    f"[factory-bench] runtime.v2 event wait failed: {run_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                return _timeout_diagnostic(
                    kind="runtime_v2_connection_failed",
                    message=str(exc),
                )
            reconnect_attempt += 1
            backoff = min(
                EVENT_WAIT_RECONNECT_BACKOFF_CAP_S,
                EVENT_WAIT_RECONNECT_BACKOFF_BASE_S * (2 ** min(reconnect_attempt - 1, 4)),
            )
            sleep_s = min(backoff, remaining)
            print(
                f"[factory-bench] runtime.v2 event wait disconnected: {run_id}: {exc}; "
                f"reconnect attempt {reconnect_attempt} in {sleep_s:.1f}s "
                f"({remaining:.1f}s budget left)",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(sleep_s)
            continue

        # Soft disconnect path (TimeoutError branch) also back off before reconnect.
        remaining = deadline - loop.time()
        if remaining <= 0:
            continue
        reconnect_attempt += 1
        backoff = min(
            EVENT_WAIT_RECONNECT_BACKOFF_CAP_S,
            EVENT_WAIT_RECONNECT_BACKOFF_BASE_S * (2 ** min(reconnect_attempt - 1, 4)),
        )
        await asyncio.sleep(min(backoff, remaining))


def _event_wait_diagnostic_status(
    run_id: str,
    latest_status: Mapping[str, Any],
    *,
    kind: str,
    message: str,
    backend_url: str,
    workspace: str,
) -> dict[str, Any]:
    status = dict(latest_status)
    status["run_id"] = run_id
    status.setdefault("status", "unknown")
    status.setdefault("phase", kind or "event_wait_failed")
    status["_event_wait_error"] = {
        "kind": kind,
        "message": message,
        "backend_url": backend_url,
        "workspace": workspace,
    }
    status["last_observed_status"] = {
        key: value
        for key, value in dict(latest_status).items()
        if key not in {"_event_wait_error", "last_observed_status"}
    }
    return status
