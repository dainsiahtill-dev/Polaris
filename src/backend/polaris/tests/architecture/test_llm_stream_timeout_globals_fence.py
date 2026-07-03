"""Fences for retired LLM stream timeout global exports."""

from __future__ import annotations

import polaris.kernelone.llm._timeout_config as timeout_config
import polaris.kernelone.llm.engine.stream as stream
import polaris.kernelone.llm.engine.stream.config as stream_config

_RETIRED_CONFIG_EXPORTS = {
    "INVOKE_TIMEOUT_SEC",
    "MAX_BUFFER_SIZE",
    "_MAX_PENDING_TOOL_CALLS",
    "_STREAM_TIMEOUT",
    "_TOKEN_TIMEOUT",
}

_RETIRED_STREAM_TIMEOUT_PROXIES = {
    "get_stream_timeout",
    "reset_stream_timeout",
    "set_stream_timeout",
}


def test_timeout_config_exposes_function_api_only() -> None:
    """The unified timeout module must not publish stale module-level aliases."""
    for name in _RETIRED_CONFIG_EXPORTS:
        assert not hasattr(timeout_config, name), name
        assert name not in timeout_config.__all__

    for name in {"get_invoke_timeout", "get_stream_timeout", "get_token_timeout"}:
        assert hasattr(timeout_config, name), name
        assert name in timeout_config.__all__


def test_stream_config_does_not_reintroduce_timeout_globals_or_proxy_functions() -> None:
    """StreamConfig is the stream API; timeout functions live in _timeout_config."""
    for name in _RETIRED_CONFIG_EXPORTS | _RETIRED_STREAM_TIMEOUT_PROXIES:
        assert not hasattr(stream_config, name), name


def test_stream_package_roots_do_not_reexport_retired_timeout_surface() -> None:
    """The package-root stream surface must not keep a second timeout fact source alive."""
    for name in _RETIRED_CONFIG_EXPORTS | _RETIRED_STREAM_TIMEOUT_PROXIES:
        assert not hasattr(stream, name), f"{stream.__name__}.{name}"
        assert name not in stream.__all__
