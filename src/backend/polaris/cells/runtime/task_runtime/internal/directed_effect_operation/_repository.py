"""DirectedEffectOperationRepository loaded from assembled class source."""

from __future__ import annotations

import contextlib
import sys as _sys
from pathlib import Path

# Import helpers into this module so class methods resolve free names at runtime
# when bound through this module's globals... actually methods use defining-module globals.
# The class is exec'd into a namespace seeded with helpers + external imports.
from polaris.cells.events.fact_stream.public import (  # noqa: F401
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamError,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    ReadGuardedFactSnapshotCommandV1,
)

from ...public.contracts import *  # noqa: F403
from ._helpers import *  # noqa: F403


def _pkg_lookup(name: str):
    """Resolve name from the package module (supports test monkeypatching)."""

    return getattr(_sys.modules[__package__], name)


def query_fact_events(*args, **kwargs):  # type: ignore[no-redef]
    return _pkg_lookup("query_fact_events")(*args, **kwargs)


def read_guarded_fact_snapshot(*args, **kwargs):  # type: ignore[no-redef]
    return _pkg_lookup("read_guarded_fact_snapshot")(*args, **kwargs)


def append_if_guarded_snapshot(*args, **kwargs):  # type: ignore[no-redef]
    return _pkg_lookup("append_if_guarded_snapshot")(*args, **kwargs)


def append_fact_event(*args, **kwargs):  # type: ignore[no-redef]
    return _pkg_lookup("append_fact_event")(*args, **kwargs)


def enroll_fact_stream_streams(*args, **kwargs):  # type: ignore[no-redef]
    return _pkg_lookup("enroll_fact_stream_streams")(*args, **kwargs)


_SOURCE_PATH = Path(__file__).with_name("_repository_class.source")
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")

_NS: dict = dict(globals())
_NS["__name__"] = __name__
_NS["__file__"] = str(_SOURCE_PATH)
_NS["__package__"] = __package__

exec(compile(_SOURCE, str(_SOURCE_PATH), "exec"), _NS)

DirectedEffectOperationRepository = _NS["DirectedEffectOperationRepository"]
# Point class at package module so getsource(class) uses package __file__ (surface).
DirectedEffectOperationRepository.__module__ = __package__  # type: ignore[misc]
DirectedEffectOperationRepository.__qualname__ = "DirectedEffectOperationRepository"

# Re-bind method __module__ for clarity (getsource(method) still uses co_filename=.source)
for _name, _attr in list(vars(DirectedEffectOperationRepository).items()):
    _fn = getattr(_attr, "__func__", _attr)
    if callable(_fn) and getattr(_fn, "__module__", None) is not None:
        with contextlib.suppress(AttributeError, TypeError):
            _fn.__module__ = __package__  # type: ignore[misc]

__all__ = ["DirectedEffectOperationRepository"]
