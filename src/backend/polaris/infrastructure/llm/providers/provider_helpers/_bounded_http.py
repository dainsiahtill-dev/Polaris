"""Total-deadline HTTP behind the existing sync response contract."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import ssl
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import requests
from certifi import where as requests_ca_bundle
from polaris.kernelone.llm.engine.invocation_budget import get_provider_invocation_budget

logger = logging.getLogger(__name__)


def _timeouts(timeout: Any) -> tuple[float | None, float | None]:
    if timeout is None:
        return None, None
    if isinstance(timeout, bool):
        raise ValueError("HTTP timeout cannot be boolean")
    if isinstance(timeout, (int, float)):
        value = float(timeout)
        if not math.isfinite(value):
            raise ValueError("HTTP timeout must be finite")
        return (value, value) if value > 0 else (None, None)
    if isinstance(timeout, tuple) and len(timeout) == 2:
        connect, read = (float(value) for value in timeout)
        if not all(math.isfinite(value) and value > 0 for value in (connect, read)):
            raise ValueError("HTTP connect/read timeouts must be positive and finite")
        return connect, read
    raise ValueError("invalid HTTP timeout")


def _proxy_and_ssl(url: str) -> tuple[str | None, ssl.SSLContext]:
    proxy = requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url))
    if proxy and urlsplit(proxy).scheme.lower() not in {"http", "https"}:
        raise requests.ConnectionError("unsupported_provider_proxy_scheme")
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or requests_ca_bundle()
    context = ssl.create_default_context(
        capath=bundle if Path(bundle).is_dir() else None, cafile=None if Path(bundle).is_dir() else bundle
    )
    return proxy, context


class _BufferedResponse(requests.Response):
    _content_consumed: bool


async def _post(url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: Any) -> requests.Response:
    connect, total = _timeouts(timeout)
    budget = get_provider_invocation_budget()
    if budget is not None:
        remaining = budget.remaining()
        total = remaining if total is None else min(total, remaining)
    if total is not None and connect is not None:
        connect = min(connect, total)
    proxy, ssl_context = _proxy_and_ssl(url)
    client_timeout = aiohttp.ClientTimeout(total=total, connect=connect, sock_read=total, ceil_threshold=math.inf)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None

    def cancel_transport() -> None:
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    unsubscribe = budget.on_cancel(cancel_transport) if budget else None
    start = time.monotonic()
    try:
        async with (
            aiohttp.ClientSession(
                timeout=client_timeout,
                json_serialize=lambda value: json.dumps(value, allow_nan=False),
            ) as session,
            session.post(
                url, headers=dict(headers), json=dict(payload), proxy=proxy, ssl=ssl_context, timeout=client_timeout
            ) as response,
        ):
            content = await response.read()
            result = _BufferedResponse()
            result.status_code = response.status
            result.headers = requests.structures.CaseInsensitiveDict(response.headers)
            result._content = content
            result._content_consumed = True
            result.encoding = requests.utils.get_encoding_from_headers(result.headers)
            result.url = str(response.url)
            result.reason = response.reason or ""
            result.elapsed = timedelta(seconds=time.monotonic() - start)
            logger.info(
                "provider.physical_http_body_completed status=%s duration_ms=%s",
                response.status,
                int((time.monotonic() - start) * 1000),
            )
            return result
    except asyncio.CancelledError:
        logger.info("provider.physical_http_body_cancelled duration_ms=%s", int((time.monotonic() - start) * 1000))
        if budget and budget.stop_reason == "deadline":
            raise requests.Timeout("provider_invocation_deadline") from None
        raise
    except (asyncio.TimeoutError, TimeoutError) as error:
        logger.info("provider.physical_http_body_timeout duration_ms=%s", int((time.monotonic() - start) * 1000))
        raise requests.Timeout("provider_physical_http_total_timeout") from error
    except aiohttp.ClientError as error:
        raise requests.ConnectionError(str(error)) from error
    finally:
        if unsubscribe:
            unsubscribe()


def bounded_http_post(
    url: str, *, headers: Mapping[str, str], json: Mapping[str, Any], timeout: Any
) -> requests.Response:
    """Run one local async transport; physical gate stays outside this helper."""
    return asyncio.run(_post(url, headers, json, timeout))
