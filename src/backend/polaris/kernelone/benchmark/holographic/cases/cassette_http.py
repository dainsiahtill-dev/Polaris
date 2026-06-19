"""Cassette / HTTP-intercept benchmark executors (TC-CM-001..004).

HTTP patch overhead, cache-replay recording fidelity, sanitization
redaction, and shadow-player strict replay.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    SanitizationHook,
)
from polaris.kernelone.benchmark.holographic.stats import _perf_ms
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples
from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
    HTTPRequest,
    HTTPResponse,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    UnrecordedRequestError,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
    HTTPExchange,
    apply_http_patch,
    remove_http_patch,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.player import (
    ShadowPlayer,
)
from polaris.kernelone.benchmark.reproducibility.vcr import CacheReplay


class _AsyncDelayTransport(httpx.AsyncBaseTransport):
    def __init__(self, delay_s: float = 0.0015) -> None:
        self._delay_s = max(0.0, float(delay_s))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self._delay_s)
        return httpx.Response(status_code=200, json={"ok": True}, request=request)


async def _request_latency_samples(*, patched: bool, concurrency: int) -> list[float]:
    from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
        clear_interceptor,
        set_interceptor,
    )

    latencies_ms: list[float] = []
    transport = _AsyncDelayTransport(delay_s=0.0015)
    if patched:
        await apply_http_patch()

        async def passthrough(_exchange: HTTPExchange) -> tuple[bool, httpx.Response | None]:
            return (True, None)

        set_interceptor(passthrough)

    try:
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:

            async def make_request(index: int) -> None:
                started = time.perf_counter_ns()
                response = await client.get(f"https://benchmark.local/{index}")
                _ = response.status_code
                latencies_ms.append(_perf_ms(started))

            await asyncio.gather(*(make_request(index) for index in range(concurrency)))
    finally:
        if patched:
            clear_interceptor()
            await remove_http_patch()
    return latencies_ms


async def _exec_tc_cm_001(case: HolographicCase) -> dict[str, float]:
    patch_samples_us: list[float] = []
    for _ in range(max(200, case.min_samples)):
        started = time.perf_counter_ns()
        await apply_http_patch()
        await remove_http_patch()
        patch_samples_us.append((time.perf_counter_ns() - started) / 1000.0)

    baseline = await _request_latency_samples(patched=False, concurrency=200)
    patched = await _request_latency_samples(patched=True, concurrency=200)
    baseline_stats = summarize_samples(baseline, warmup_rounds=case.warmup_rounds)
    patched_stats = summarize_samples(patched, warmup_rounds=case.warmup_rounds)
    patch_stats = summarize_samples(patch_samples_us, warmup_rounds=case.warmup_rounds)
    added_latency = (
        ((patched_stats.p99 - baseline_stats.p99) / baseline_stats.p99) * 100.0 if baseline_stats.p99 > 0 else 0.0
    )
    throughput = 200.0 / max(sum(patched) / 1000.0, 1e-9)
    return {
        "patch_restore_overhead_us": patch_stats.p99,
        "added_latency_percent": added_latency,
        "throughput_req_s": throughput,
    }


async def _exec_tc_cm_002(case: HolographicCase) -> dict[str, float]:
    import tempfile

    sample_count = max(300, min(case.min_samples, 1000))
    methods = ["GET", "POST", "PUT", "DELETE"]
    expected_request_body: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="holo-cm-002-") as directory:
        replay = CacheReplay(cache_dir=directory, mode="both")
        for index in range(sample_count):
            method = methods[index % len(methods)]
            key = f"cm2-{index}"
            body = json.dumps({"index": index, "method": method}, ensure_ascii=False)
            expected_request_body[key] = body
            replay.record(
                key=key,
                response={"ok": True, "index": index},
                method=method,
                url=f"https://api.example.com/{index}",
                request_headers={"X-Test": "1"},
                request_body=body,
                response_status=200 + (index % 5),
                response_headers={"Content-Type": "application/json"},
                latency_ms=1.5,
            )

        recordings = sorted(replay.list_recordings(), key=lambda item: item.timestamp)
        complete_fields = 0
        body_exact = 0
        timestamp_order = 0
        for idx, recording in enumerate(recordings):
            if (
                recording.method
                and recording.url
                and recording.request_headers
                and recording.request_body
                and recording.response_status > 0
                and recording.response_headers
                and recording.timestamp
            ):
                complete_fields += 1
            if recording.request_body == expected_request_body.get(recording.request_key, ""):
                body_exact += 1
            if idx == 0 or recording.timestamp >= recordings[idx - 1].timestamp:
                timestamp_order += 1

    total = max(len(recordings), 1)
    return {
        "field_completeness_percent": (complete_fields / total) * 100.0,
        "request_body_exact_percent": (body_exact / total) * 100.0,
        "timestamp_order_percent": (timestamp_order / total) * 100.0,
    }


async def _exec_tc_cm_003(case: HolographicCase) -> dict[str, float]:
    sanitizer = SanitizationHook()
    payloads = [
        {"Authorization": "Bearer sk-1234567890abcdef"},
        {"api_key": "secret123456"},
        {"jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"},
        {"token": "YXNkZmdoamtsbW5vcHFyc3R1dnd4eXo="},
    ]
    safe_payloads = [
        {"method": "GET"},
        {"url_path": "/v1/chat/completions"},
        {"status_code": 200},
    ]
    sensitive_total = 0
    sensitive_redacted = 0
    token_total = 0
    token_redacted = 0
    for sensitive_item in payloads:
        sanitized = sanitizer.sanitize(sensitive_item)
        for key in sensitive_item:
            sensitive_total += 1
            if sanitized.get(key) == "[REDACTED]":
                sensitive_redacted += 1
            if key in {"jwt", "token", "Authorization"}:
                token_total += 1
                if sanitized.get(key) == "[REDACTED]":
                    token_redacted += 1

    safe_total = len(safe_payloads)
    safe_retained = 0
    for safe_item in safe_payloads:
        sanitized = sanitizer.sanitize(safe_item)
        if sanitized == safe_item:
            safe_retained += 1

    return {
        "sensitive_redaction_percent": (sensitive_redacted / max(sensitive_total, 1)) * 100.0,
        "nonsensitive_retention_percent": (safe_retained / max(safe_total, 1)) * 100.0,
        "token_recall_percent": (token_redacted / max(token_total, 1)) * 100.0,
    }


async def _exec_tc_cm_004(case: HolographicCase) -> dict[str, float]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="holo-cm-004-") as directory:
        cassette = Cassette(cassette_id="cm-004", cassette_dir=Path(directory), mode="replay")
        for index in range(100):
            cassette.add_entry(
                request=HTTPRequest.from_raw(
                    method="GET",
                    url=f"https://recorded.local/{index}",
                    headers={"X-Recorded": "1"},
                    body=None,
                ),
                response=HTTPResponse.from_raw(
                    status_code=200,
                    headers={"Content-Type": "application/json"},
                    body=b'{"ok": true}',
                ),
                latency_ms=1.0,
            )

        player = ShadowPlayer(cassette=cassette, strict=True)
        await player.start()
        recorded_ok = 0
        unrecorded_errors = 0
        silent_bypass = 0

        try:
            for index in range(50):
                should_proceed, response = await player.intercept(
                    HTTPExchange(
                        method="GET",
                        url=f"https://recorded.local/{index}",
                        headers={},
                        body=None,
                        response_status=0,
                        response_headers={},
                        response_body=None,
                        latency_ms=0.0,
                    )
                )
                if not should_proceed and response is not None and response.status_code == 200:
                    recorded_ok += 1

            for index in range(50):
                try:
                    await player.intercept(
                        HTTPExchange(
                            method="GET",
                            url=f"https://unrecorded.local/{index}",
                            headers={},
                            body=None,
                            response_status=0,
                            response_headers={},
                            response_body=None,
                            latency_ms=0.0,
                        )
                    )
                except UnrecordedRequestError:
                    unrecorded_errors += 1
                else:
                    silent_bypass += 1
        finally:
            await player.stop()

    return {
        "recorded_success_percent": (recorded_ok / 50.0) * 100.0,
        "unrecorded_error_percent": (unrecorded_errors / 50.0) * 100.0,
        "silent_bypass_percent": (silent_bypass / 50.0) * 100.0,
    }
