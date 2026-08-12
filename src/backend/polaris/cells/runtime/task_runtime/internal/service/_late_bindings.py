"""Late-bound package symbols for test monkeypatch compatibility.

Characterization tests patch attributes on
``polaris.cells.runtime.task_runtime.internal.service`` (the package). Mixin
modules must not bind the underlying implementations at import time; they
resolve through this module so monkeypatches on the package surface win.
"""

from __future__ import annotations

import sys
from typing import Any

from polaris.cells.events.fact_stream.public.service import (
    append_fact_event as _append_fact_event_default,
    query_fact_events as _query_fact_events_default,
    query_fact_stream_head as _query_fact_stream_head_default,
)
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    utc_now as _utc_now_default,
    utc_now_iso as _utc_now_iso_default,
)

_PACKAGE_NAME = "polaris.cells.runtime.task_runtime.internal.service"


def _package_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(_PACKAGE_NAME)
    if pkg is None:
        return default
    value = pkg.__dict__.get(name, default)
    # Avoid recursion if a wrapper was accidentally re-exported on the package.
    if value is default:
        return default
    # If package holds a different callable (including monkeypatch), use it.
    return value


def append_fact_event(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to package ``append_fact_event`` (monkeypatch-aware)."""
    fn = _package_attr("append_fact_event", _append_fact_event_default)
    if fn is append_fact_event:
        fn = _append_fact_event_default
    return fn(*args, **kwargs)


def query_fact_events(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to package ``query_fact_events`` (monkeypatch-aware)."""
    fn = _package_attr("query_fact_events", _query_fact_events_default)
    if fn is query_fact_events:
        fn = _query_fact_events_default
    return fn(*args, **kwargs)


def query_fact_stream_head(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to package ``query_fact_stream_head`` (monkeypatch-aware)."""
    fn = _package_attr("query_fact_stream_head", _query_fact_stream_head_default)
    if fn is query_fact_stream_head:
        fn = _query_fact_stream_head_default
    return fn(*args, **kwargs)


def utc_now(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to package ``utc_now`` (monkeypatch-aware)."""
    fn = _package_attr("utc_now", _utc_now_default)
    if fn is utc_now:
        fn = _utc_now_default
    return fn(*args, **kwargs)


def utc_now_iso(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to package ``utc_now_iso`` (monkeypatch-aware)."""
    fn = _package_attr("utc_now_iso", _utc_now_iso_default)
    if fn is utc_now_iso:
        fn = _utc_now_iso_default
    return fn(*args, **kwargs)
