from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_TIMEOUT_S = 10.0


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
) -> dict[str, Any] | None:
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8") or "{}"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        print(f"[factory-bench] backend POST failed: {url}: {exc}", file=sys.stderr, flush=True)
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
) -> dict[str, Any] | None:
    try:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8") or "{}"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        print(f"[factory-bench] backend GET failed: {url}: {exc}", file=sys.stderr, flush=True)
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
    return _http_post_json(f"{backend_url}/v2/factory/runs", payload, token=token)


def get_run_status(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}", token=token)


def get_audit_bundle(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}/audit-bundle", token=token)


def get_run_artifacts(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}/artifacts", token=token)


def poll_run_until_terminal(
    backend_url: str,
    run_id: str,
    token: str = "",
    poll_interval_s: float = 5.0,
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while True:
        status = get_run_status(backend_url, run_id, token=token)
        if status is not None and on_status is not None:
            on_status(status)
        if status is None:
            if time.monotonic() >= deadline:
                print(f"[factory-bench] poll timeout: {run_id}", file=sys.stderr, flush=True)
                return None
            time.sleep(poll_interval_s)
            continue
        current_status = status.get("status", "")
        if current_status in ("completed", "failed", "cancelled"):
            return status
        if time.monotonic() >= deadline:
            print(f"[factory-bench] poll timeout: {run_id}", file=sys.stderr, flush=True)
            return None
        time.sleep(poll_interval_s)
