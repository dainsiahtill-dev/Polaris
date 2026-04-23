"""Debug Trace - 全局调试追踪模块

提供 HTTP 请求、子进程、流式输出的全局追踪能力。

状态管理策略（重构后）：
- 所有可变状态封装进 DebugTracer 类，支持 install/uninstall 对称操作，
  测试可完全隔离。
- 模块级 API（emit_debug_event, install_global_debug_hooks 等）委托给
  一个默认实例 _default_tracer，保持向后兼容。
- 测试应直接实例化 DebugTracer() 并调用 install/uninstall，而非使用
  模块级 API，从而避免交叉污染。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from polaris.kernelone.utils import utc_now_iso

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

# =============================================================================
# 常量定义（不可变）
# =============================================================================

_SENSITIVE_HINTS = (
    "authorization",
    "api-key",
    "api_key",
    "secret",
    "token",
    "password",
    "cookie",
)
_KEY_CONTEXT_HINTS = frozenset(
    {
        "api",
        "secret",
        "private",
        "access",
        "auth",
        "client",
        "session",
        "signing",
        "encrypt",
        "credential",
    }
)


# =============================================================================
# 工具函数（纯函数，无状态）
# =============================================================================


def _now_utc() -> str:
    return utc_now_iso()


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _trace_id(prefix: str = "trace") -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    if any(hint in lowered for hint in _SENSITIVE_HINTS):
        return True
    if "key" not in lowered:
        return False

    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    for idx, token in enumerate(tokens):
        if token in {"key", "keys"}:
            neighbors = []
            if idx > 0:
                neighbors.append(tokens[idx - 1])
            if idx + 1 < len(tokens):
                neighbors.append(tokens[idx + 1])
            if any(item in _KEY_CONTEXT_HINTS for item in neighbors):
                return True
            continue

        if token.endswith("key") and token != "monkey":
            prefix = token[:-3]
            if prefix in _KEY_CONTEXT_HINTS:
                return True
            if any(prefix.endswith(hint) for hint in _KEY_CONTEXT_HINTS):
                return True
    return False


def _truncate_text(text: str, limit: int = 2000) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    keep = max(32, limit - 32)
    return text[:keep] + f"...<truncated {len(text) - keep} chars>"


def _parse_json_text(text: str, *, max_chars: int = 120000) -> Any | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if len(candidate) > max_chars:
        return None
    if candidate[0] not in "{[":
        return None
    try:
        return json.loads(candidate)
    except (RuntimeError, ValueError):
        return None


def _mask_text(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}***{value[-3:]}"


def _to_preview(value: Any, *, limit: int = 2000, depth: int = 0, key_hint: str = "") -> Any:
    if depth > 3:
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, (bytes, bytearray)):
        text = value.decode("utf-8", errors="replace")
        if key_hint and _is_sensitive_key(key_hint):
            return _mask_text(text)
        parsed = _parse_json_text(text)
        if parsed is not None:
            return _to_preview(parsed, limit=limit, depth=depth + 1, key_hint=key_hint)
        return _truncate_text(text, limit=limit)

    if isinstance(value, str):
        if key_hint and _is_sensitive_key(key_hint):
            return _mask_text(value)
        parsed = _parse_json_text(value)
        if parsed is not None:
            return _to_preview(parsed, limit=limit, depth=depth + 1, key_hint=key_hint)
        return _truncate_text(value, limit=limit)

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 40:
                out["__truncated__"] = f"{len(value) - idx} more keys"
                break
            key = str(k)
            out[key] = _to_preview(v, limit=limit, depth=depth + 1, key_hint=key)
        return out

    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        out_list = [_to_preview(item, limit=limit, depth=depth + 1, key_hint=key_hint) for item in seq[:40]]
        if len(seq) > 40:
            out_list.append(f"<truncated {len(seq) - 40} items>")
        return out_list

    return _truncate_text(str(value), limit=limit)


def _sanitize_headers(headers: Any) -> dict[str, Any]:
    if headers is None:
        return {}
    try:
        items = headers.items()
    except (RuntimeError, ValueError):
        return {}

    out: dict[str, Any] = {}
    for key, value in items:
        key_str = str(key)
        if _is_sensitive_key(key_str):
            out[key_str] = _mask_text(str(value))
        else:
            out[key_str] = _truncate_text(str(value), limit=512)
    return out


# 向后兼容的公开名称（旧代码可能直接 import sanitize_headers）
sanitize_headers = _sanitize_headers


def _extract_cmd(args: Iterable[Any], kwargs: dict[str, Any]) -> Any:
    args_list = list(args)
    if args_list:
        return args_list[0]
    return kwargs.get("args")


# =============================================================================
# DebugTracer 类（封装所有可变状态）
# =============================================================================


@dataclass
class _PatchState:
    """封装单个 DebugTracer 实例的补丁状态。"""

    lock: threading.Lock = field(default_factory=threading.Lock)
    patched: bool = False
    enabled: bool = False
    orig_requests_session_request: Callable[..., Any] | None = None
    orig_aiohttp_session_request: Callable[..., Any] | None = None
    orig_urlopen: Callable[..., Any] | None = None
    orig_subprocess_run: Callable[..., Any] | None = None
    orig_subprocess_popen: type | None = None


class DebugTracer:
    """可卸载的调试追踪器。

    封装所有补丁状态，支持 install/uninstall 对称操作。
    测试时直接实例化本类并调用 install/uninstall，可完全隔离全局状态。

    Example::

        tracer = DebugTracer()
        tracer.install()
        tracer.set_enabled(True)
        # ... run code under test ...
        tracer.uninstall()  # 完全恢复原始函数
    """

    def __init__(self) -> None:
        self._state = _PatchState()
        # 保存原始 subprocess 引用用于恢复
        self._state.orig_subprocess_run = subprocess.run
        self._state.orig_subprocess_popen = subprocess.Popen

    # -------------------------------------------------------------------------
    # 公共 API
    # -------------------------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        return self._state.enabled

    def set_enabled(self, enabled: bool) -> None:
        """启用/禁用事件输出（不影响补丁状态）。"""
        with self._state.lock:
            self._state.enabled = bool(enabled)
        os.environ["KERNELONE_DEBUG_TRACING"] = "1" if enabled else "0"
        self.emit_event("debug.toggle", enabled=enabled)

    def is_installed(self) -> bool:
        """检查是否已安装补丁。"""
        with self._state.lock:
            return self._state.patched

    def install(self) -> None:
        """安装全局调试钩子（幂等）。"""
        with self._state.lock:
            if self._state.patched:
                return
            self._patch_requests()
            self._patch_aiohttp()
            self._patch_urllib()
            self._patch_subprocess()
            self._state.patched = True

    def uninstall(self) -> None:
        """卸载全局调试钩子，恢复原始函数。"""
        with self._state.lock:
            if not self._state.patched:
                return
            self._unpatch_all()
            self._state.patched = False

    def configure(self, enabled: bool | None = None) -> None:
        """安装钩子并根据环境或参数启用追踪。"""
        self.install()
        if enabled is None:
            enabled = _truthy_env("KERNELONE_DEBUG_TRACING")
        self.set_enabled(bool(enabled))

    def __enter__(self) -> DebugTracer:
        """Enter context manager: install patches and return self."""
        self.install()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager: uninstall patches to restore original functions."""
        self.uninstall()

    def emit_event(self, event: str, **payload: Any) -> None:
        """输出调试事件（仅在 enabled=True 时生效）。"""
        with self._state.lock:
            if not self._state.enabled:
                return

        obj: dict[str, Any] = {
            "source": "polaris.debug_trace",
            "event": event,
            "ts": _now_utc(),
        }
        for key, value in payload.items():
            obj[key] = _to_preview(value, key_hint=key)

        try:
            print(json.dumps(obj, ensure_ascii=False, indent=2), flush=True)
        except (OSError, RuntimeError, ValueError):
            logger.warning("debug_trace: failed to emit event to stdout: %s", event, exc_info=True)

    def log_stream_token(
        self,
        stream_name: str,
        token: str,
        *,
        trace_id: str | None = None,
        index: int | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """记录流式 token。"""
        self.emit_event(
            "stream.token",
            trace_id=trace_id or _trace_id("stream"),
            stream=stream_name,
            token_preview=_truncate_text(token, limit=400),
            token_length=len(token or ""),
            index=index,
            elapsed_ms=elapsed_ms,
        )

    # -------------------------------------------------------------------------
    # 内部补丁实现
    # -------------------------------------------------------------------------

    def _patch_requests(self) -> None:
        try:
            import requests
        except (RuntimeError, ValueError):
            logger.warning("Failed to patch requests module for tracing")
            return

        if self._state.orig_requests_session_request is not None:
            return

        self._state.orig_requests_session_request = requests.sessions.Session.request

        tracer = self

        def traced_request(session: Any, method: str, url: str, **kwargs: Any) -> Any:
            trace = _trace_id("http")
            start = time.perf_counter()
            stream = bool(kwargs.get("stream"))
            tracer.emit_event(
                "http.out.request",
                trace_id=trace,
                client="requests",
                method=str(method).upper(),
                url=str(url),
                headers=_sanitize_headers(kwargs.get("headers")),
                params=kwargs.get("params"),
                json=kwargs.get("json"),
                data=kwargs.get("data"),
                timeout=kwargs.get("timeout"),
                stream=stream,
            )
            orig = tracer._state.orig_requests_session_request
            assert orig is not None
            try:
                response = orig(session, method, url, **kwargs)
            except (RuntimeError, ValueError) as exc:
                tracer.emit_event(
                    "http.out.error",
                    trace_id=trace,
                    client="requests",
                    duration_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                raise

            body_preview: str | None = None
            if not stream:
                try:
                    body_preview = response.text
                except (RuntimeError, ValueError):
                    body_preview = "<unavailable>"
            else:
                body_preview = "<stream-response>"

            tracer.emit_event(
                "http.out.response",
                trace_id=trace,
                client="requests",
                status_code=getattr(response, "status_code", None),
                reason=getattr(response, "reason", None),
                duration_ms=_elapsed_ms(start),
                headers=_sanitize_headers(getattr(response, "headers", None)),
                body_preview=body_preview,
            )
            return response

        requests.sessions.Session.request = traced_request  # type: ignore[assignment]

    def _patch_aiohttp(self) -> None:
        try:
            import aiohttp
        except (RuntimeError, ValueError):
            logger.warning("Failed to patch aiohttp module for tracing")
            return

        if self._state.orig_aiohttp_session_request is not None:
            return

        self._state.orig_aiohttp_session_request = aiohttp.ClientSession._request

        tracer = self

        async def traced_request(session: Any, method: str, str_or_url: str, **kwargs: Any) -> Any:
            trace = _trace_id("http")
            start = time.perf_counter()
            tracer.emit_event(
                "http.out.request",
                trace_id=trace,
                client="aiohttp",
                method=str(method).upper(),
                url=str(str_or_url),
                headers=_sanitize_headers(kwargs.get("headers")),
                params=kwargs.get("params"),
                json=kwargs.get("json"),
                data=kwargs.get("data"),
                timeout=str(kwargs.get("timeout") or ""),
            )
            orig = tracer._state.orig_aiohttp_session_request
            assert orig is not None
            try:
                response = await orig(session, method, str_or_url, **kwargs)
            except (RuntimeError, ValueError) as exc:
                tracer.emit_event(
                    "http.out.error",
                    trace_id=trace,
                    client="aiohttp",
                    duration_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                raise

            tracer.emit_event(
                "http.out.response",
                trace_id=trace,
                client="aiohttp",
                status_code=getattr(response, "status", None),
                reason=getattr(response, "reason", None),
                duration_ms=_elapsed_ms(start),
                headers=_sanitize_headers(getattr(response, "headers", None)),
                body_preview="<stream-or-deferred-body>",
            )
            return response

        aiohttp.ClientSession._request = traced_request  # type: ignore[assignment]

    def _patch_urllib(self) -> None:
        if self._state.orig_urlopen is not None:
            return

        self._state.orig_urlopen = urllib.request.urlopen

        tracer = self

        def traced_urlopen(url: Any, data: Any = None, timeout: Any = None, *args: Any, **kwargs: Any) -> Any:
            trace = _trace_id("http")
            start = time.perf_counter()

            method = "GET"
            request_url = str(url)
            headers: dict[str, Any] = {}
            body = data

            if isinstance(url, urllib.request.Request):
                request_url = str(url.full_url)
                method = str(url.get_method() or "GET").upper()
                headers = dict(url.header_items())
                if body is None:
                    body = url.data
            else:
                method = "POST" if data is not None else "GET"

            tracer.emit_event(
                "http.out.request",
                trace_id=trace,
                client="urllib",
                method=method,
                url=request_url,
                headers=_sanitize_headers(headers),
                data=body,
                timeout=timeout,
            )
            orig = tracer._state.orig_urlopen
            assert orig is not None
            try:
                response = orig(url, data=data, timeout=timeout, *args, **kwargs)
            except (RuntimeError, ValueError) as exc:
                tracer.emit_event(
                    "http.out.error",
                    trace_id=trace,
                    client="urllib",
                    duration_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                raise

            status: int | None = None
            reason: str | None = None
            response_headers: Any = None
            try:
                status = response.getcode()
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] debug_trace.py silent exception", exc)
            try:
                reason = getattr(response, "reason", None)
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] debug_trace.py silent exception", exc)
            try:
                response_headers = response.headers
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] debug_trace.py silent exception", exc)

            tracer.emit_event(
                "http.out.response",
                trace_id=trace,
                client="urllib",
                status_code=status,
                reason=reason,
                duration_ms=_elapsed_ms(start),
                headers=_sanitize_headers(response_headers),
                body_preview="<deferred-body>",
            )
            return response

        urllib.request.urlopen = traced_urlopen  # type: ignore[assignment]

    class _LoggedStreamProxy:
        def __init__(self, tracer: DebugTracer, wrapped: Any, trace_id: str, stream_name: str) -> None:
            self._tracer = tracer
            self._wrapped = wrapped
            self._trace_id = trace_id
            self._stream_name = stream_name

        def _emit(self, chunk: Any) -> None:
            if chunk is None:
                return
            if isinstance(chunk, (bytes, bytearray)):
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
            else:
                text = str(chunk)
                if not text:
                    return
            self._tracer.emit_event(
                "cli.stream",
                trace_id=self._trace_id,
                stream=self._stream_name,
                chunk_preview=_truncate_text(text, limit=500),
            )

        def readline(self, *args: Any, **kwargs: Any) -> Any:
            line = self._wrapped.readline(*args, **kwargs)
            self._emit(line)
            return line

        def read(self, *args: Any, **kwargs: Any) -> Any:
            data = self._wrapped.read(*args, **kwargs)
            self._emit(data)
            return data

        def __iter__(self) -> DebugTracer._LoggedStreamProxy:
            return self

        def __next__(self) -> Any:
            line = next(self._wrapped)
            self._emit(line)
            return line

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    def _patch_subprocess(self) -> None:
        if subprocess.Popen is not self._state.orig_subprocess_popen:
            # Already patched by another tracer; skip to avoid double-patch.
            return

        tracer = self
        orig_popen = self._state.orig_subprocess_popen
        orig_run = self._state.orig_subprocess_run
        assert orig_popen is not None and orig_run is not None

        class TracedPopen(orig_popen):  # type: ignore[misc,valid-type]
            def __init__(inner_self, *args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
                inner_self._hp_trace_id = _trace_id("cli")
                inner_self._hp_start = time.perf_counter()
                inner_self._hp_exit_logged = False
                cmd = _extract_cmd(args, kwargs)

                tracer.emit_event(
                    "cli.popen.spawn",
                    trace_id=inner_self._hp_trace_id,
                    command=cmd,
                    cwd=kwargs.get("cwd"),
                    shell=bool(kwargs.get("shell")),
                )

                try:
                    super().__init__(*args, **kwargs)
                except (RuntimeError, ValueError) as exc:
                    tracer.emit_event(
                        "cli.popen.error",
                        trace_id=inner_self._hp_trace_id,
                        command=cmd,
                        duration_ms=_elapsed_ms(inner_self._hp_start),
                        error=str(exc),
                    )
                    raise

                if inner_self.stdout is not None:  # type: ignore[has-type]
                    try:
                        inner_self.stdout = tracer._LoggedStreamProxy(  # type: ignore[has-type]
                            tracer,
                            inner_self.stdout,
                            inner_self._hp_trace_id,
                            "stdout",  # type: ignore[has-type]
                        )
                    except (RuntimeError, ValueError) as e:
                        logger.debug(f"Failed to wrap stdout: {e}")
                if inner_self.stderr is not None:  # type: ignore[has-type]
                    try:
                        inner_self.stderr = tracer._LoggedStreamProxy(  # type: ignore[has-type]
                            tracer,
                            inner_self.stderr,
                            inner_self._hp_trace_id,
                            "stderr",  # type: ignore[has-type]
                        )
                    except (RuntimeError, ValueError) as e:
                        logger.debug(f"Failed to wrap stderr: {e}")

                tracer.emit_event(
                    "cli.popen.started",
                    trace_id=inner_self._hp_trace_id,
                    pid=getattr(inner_self, "pid", None),
                )

            def _emit_exit(inner_self, stdout: Any = None, stderr: Any = None) -> None:
                if inner_self._hp_exit_logged:
                    return
                code = super().poll()
                if code is None:
                    return
                inner_self._hp_exit_logged = True
                tracer.emit_event(
                    "cli.popen.exit",
                    trace_id=inner_self._hp_trace_id,
                    pid=getattr(inner_self, "pid", None),
                    returncode=code,
                    duration_ms=_elapsed_ms(inner_self._hp_start),
                    stdout_preview=stdout,
                    stderr_preview=stderr,
                )

            def wait(inner_self, *args: Any, **kwargs: Any) -> Any:
                result = super().wait(*args, **kwargs)
                inner_self._emit_exit()
                return result

            def poll(inner_self, *args: Any, **kwargs: Any) -> Any:
                result = super().poll(*args, **kwargs)
                if result is not None:
                    inner_self._emit_exit()
                return result

            def communicate(inner_self, *args: Any, **kwargs: Any) -> Any:
                stdout, stderr = super().communicate(*args, **kwargs)
                inner_self._emit_exit(stdout=stdout, stderr=stderr)
                return stdout, stderr

        def traced_run(*args: Any, **kwargs: Any) -> Any:
            trace = _trace_id("cli")
            start = time.perf_counter()
            cmd = _extract_cmd(args, kwargs)
            tracer.emit_event(
                "cli.run.request",
                trace_id=trace,
                command=cmd,
                cwd=kwargs.get("cwd"),
                shell=bool(kwargs.get("shell")),
                timeout=kwargs.get("timeout"),
            )
            try:
                result = orig_run(*args, **kwargs)
            except (RuntimeError, ValueError) as exc:
                tracer.emit_event(
                    "cli.run.error",
                    trace_id=trace,
                    command=cmd,
                    duration_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                raise
            tracer.emit_event(
                "cli.run.response",
                trace_id=trace,
                command=cmd,
                returncode=getattr(result, "returncode", None),
                duration_ms=_elapsed_ms(start),
                stdout_preview=getattr(result, "stdout", None),
                stderr_preview=getattr(result, "stderr", None),
            )
            return result

        subprocess.Popen = TracedPopen  # type: ignore[misc,assignment]
        subprocess.run = traced_run  # type: ignore[assignment]

    def _unpatch_all(self) -> None:
        """恢复所有被补丁替换的原始函数（必须在 self._state.lock 持有时调用）。"""
        # requests
        if self._state.orig_requests_session_request is not None:
            try:
                import requests

                requests.sessions.Session.request = (  # type: ignore[method-assign]
                    self._state.orig_requests_session_request
                )
            except (RuntimeError, ValueError) as exc:
                logger.debug("debug_trace: failed to restore requests.Session.request: %s", exc)
            self._state.orig_requests_session_request = None

        # aiohttp
        if self._state.orig_aiohttp_session_request is not None:
            try:
                import aiohttp

                aiohttp.ClientSession._request = self._state.orig_aiohttp_session_request  # type: ignore[method-assign]
            except (RuntimeError, ValueError) as exc:
                logger.debug("debug_trace: failed to restore aiohttp.ClientSession._request: %s", exc)
            self._state.orig_aiohttp_session_request = None

        # urllib
        if self._state.orig_urlopen is not None:
            urllib.request.urlopen = self._state.orig_urlopen  # type: ignore[assignment]
            self._state.orig_urlopen = None

        # subprocess
        if self._state.orig_subprocess_popen is not None:
            subprocess.Popen = self._state.orig_subprocess_popen  # type: ignore[misc,assignment]
        if self._state.orig_subprocess_run is not None:
            subprocess.run = self._state.orig_subprocess_run  # type: ignore[assignment]


# =============================================================================
# 默认实例（向后兼容的模块级 API）
# =============================================================================

_default_tracer = DebugTracer()


def is_debug_tracing_enabled() -> bool:
    """检查默认追踪器是否已启用。"""
    return _default_tracer.is_enabled


def set_debug_tracing_enabled(enabled: bool) -> None:
    """启用/禁用默认追踪器的事件输出。"""
    _default_tracer.set_enabled(enabled)


def configure_debug_tracing(enabled: bool | None = None) -> None:
    """配置默认追踪器（安装钩子并设置启用状态）。"""
    _default_tracer.configure(enabled)


def emit_debug_event(event: str, **payload: Any) -> None:
    """通过默认追踪器输出调试事件。"""
    _default_tracer.emit_event(event, **payload)


def install_global_debug_hooks() -> None:
    """安装默认追踪器的全局调试钩子（向后兼容）。"""
    _default_tracer.install()


def uninstall_global_debug_hooks() -> None:
    """卸载默认追踪器的全局调试钩子（新增，用于测试清理）。"""
    _default_tracer.uninstall()


def log_stream_token(
    stream_name: str,
    token: str,
    *,
    trace_id: str | None = None,
    index: int | None = None,
    elapsed_ms: int | None = None,
) -> None:
    """通过默认追踪器记录流式 token。"""
    _default_tracer.log_stream_token(stream_name, token, trace_id=trace_id, index=index, elapsed_ms=elapsed_ms)


# =============================================================================
# 工厂函数（推荐用于 DI 和测试）
# =============================================================================


def create_debug_tracer() -> DebugTracer:
    """工厂函数：创建一个新的 DebugTracer 实例。

    测试应使用本函数创建独立实例，而非依赖模块级 API，从而避免
    测试间的全局状态污染。

    Returns:
        DebugTracer: 全新的追踪器实例。
    """
    return DebugTracer()
