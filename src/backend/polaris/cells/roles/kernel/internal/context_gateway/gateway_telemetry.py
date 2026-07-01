"""Per-run telemetry emitters for :class:`RoleContextGateway`.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 2). The gateway now
calls this owner directly for per-run context telemetry.

MONKEYPATCH CONTRACT (load-bearing): the in-package tests patch
``...context_gateway.gateway.resolve_run_dir`` and ``...gateway.emit_event`` on
the **gateway module object** at call time. To keep those patches effective, the
two side-effecting callables are resolved through the gateway module namespace
here (``_gateway_module().emit_event`` / ``.resolve_run_dir``) rather than
imported directly. ``get_prefix_drift_observer`` / ``extract_prefix_slice`` are
NOT patched through the gateway module, so they are imported from their canonical
source directly.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from polaris.kernelone.context.cache_stability import (
    extract_prefix as extract_prefix_slice,
    get_prefix_drift_observer,
)
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

logger = logging.getLogger(__name__)


def _gateway_module() -> ModuleType:
    """Return the ``gateway`` module so ``emit_event`` / ``resolve_run_dir`` stay
    patchable through the gateway module namespace (test monkeypatch contract)."""
    from . import gateway as gateway_module

    return gateway_module


class GatewayTelemetry:
    """Owns the per-run context observation emitters for one gateway.

    Stateless aside from the ``workspace`` / ``role_id`` resolved from the owning
    gateway; safe to construct per gateway instance.
    """

    def __init__(self, *, workspace: Path, role_id: str) -> None:
        self._workspace = workspace
        self._role_id = role_id

    def resolve_run_events_path(self, request: ContextRequest) -> str:
        """Resolve the per-run events file for this request, or ``""`` to skip.

        Shared fail-safe resolution used by the per-run observation emitters:
        an explicit ``request.events_path`` is trusted; otherwise the per-run
        file is self-resolved from ``workspace + run_id`` via the canonical
        ``resolve_run_dir`` and emitted to ONLY if that run directory already
        exists (created by the orchestration that owns the run). A resolution
        mismatch therefore skips silently rather than writing a phantom file.
        """
        run_id = str(getattr(request, "run_id", "") or "").strip()
        if not run_id:
            return ""
        events_path = str(getattr(request, "events_path", "") or "").strip()
        if events_path:
            return events_path
        try:
            run_dir = _gateway_module().resolve_run_dir(str(self._workspace), "", run_id)
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("run events path resolve_run_dir failed: %s", exc)
            return ""
        if not run_dir or not os.path.isdir(run_dir):
            return ""
        return os.path.join(run_dir, "events", "runtime.events.jsonl")

    def emit_context_build_observation(
        self,
        request: ContextRequest,
        *,
        items_count: int,
        total_tokens: int,
        message_count: int,
        projection_id: str,
    ) -> None:
        """Emit a ``context.build`` observation to the per-run events file.

        Mirrors ``ContextEngine._emit_context_events`` so the realtime ContextOS
        dashboard surfaces projection / in-window item counts for *every* role turn
        (Director/CE/QA), not only PM planning's ``prompt_context``. The role turn
        keeps its snapshot as in-memory state and persists no snapshot file, so no
        ``context.snapshot`` receipt is emitted here (honest: no receipt fabricated).

        Fail-safe: resolves the per-run events file from ``request.events_path`` or
        from ``workspace + run_id`` via the canonical ``resolve_run_dir``; when self-
        resolving it emits ONLY if the run directory already exists (created by the
        orchestration that owns the run). A resolution mismatch therefore skips
        silently rather than writing a stray/phantom file. Observability must never
        break a turn, so all failures are swallowed.
        """
        run_id = str(getattr(request, "run_id", "") or "").strip()
        if not run_id:
            return
        events_path = str(getattr(request, "events_path", "") or "").strip()
        if not events_path:
            try:
                run_dir = _gateway_module().resolve_run_dir(str(self._workspace), "", run_id)
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                logger.debug("context.build resolve_run_dir failed: %s", exc)
                return
            if not run_dir or not os.path.isdir(run_dir):
                # No live run directory for this run_id → resolution does not match a
                # real orchestration run; skip rather than create a phantom file.
                return
            events_path = os.path.join(run_dir, "events", "runtime.events.jsonl")
        role_id = self._role_id
        try:
            _gateway_module().emit_event(
                events_path,
                kind="observation",
                actor="System",
                name="context.build",
                refs={
                    "run_id": run_id,
                    "role": role_id,
                    "task_id": getattr(request, "task_id", None),
                },
                summary=f"ContextPack built ({items_count} items)",
                output={
                    "items_count": int(items_count),
                    "total_tokens": int(total_tokens),
                    "message_count": int(message_count),
                    "projection_id": projection_id,
                    "role": role_id,
                },
            )
        except (OSError, ValueError) as exc:  # pragma: no cover - never break a turn
            logger.debug("context.build emit failed: %s", exc)

    def emit_prefix_drift_observation(
        self,
        request: ContextRequest,
        *,
        messages: Sequence[Mapping[str, Any]],
        system_prompt: str | None,
    ) -> None:
        """Emit a ``context.prefix_drift`` observation (Headroom T1-B step 1).

        NON-MUTATING. Fingerprints the cache-hot prefix (role ``system_prompt`` +
        the leading frozen ``system`` segment) and reports whether it drifted
        since this session's previous assembly, plus any volatile tokens that
        would bust the local vLLM/llama.cpp prompt cache. The session is keyed by
        ``run_id`` + role; ``ContextRequest`` has no standalone ``session_id``
        field, so ``run_id`` + role is the stable per-session identity.

        Fail-safe identically to ``emit_context_build_observation``: emits only
        when the per-run events file is resolvable, swallows all errors, and
        never breaks a turn. Pure diagnostic — request bytes are untouched.
        """
        run_id = str(getattr(request, "run_id", "") or "").strip()
        if not run_id:
            return
        events_path = self.resolve_run_events_path(request)
        if not events_path:
            return
        role_id = self._role_id
        try:
            prefix = extract_prefix_slice(messages, system_prompt)
            session_key = f"{run_id}:{role_id}"
            report = get_prefix_drift_observer().observe(session_key, prefix)
            _gateway_module().emit_event(
                events_path,
                kind="observation",
                actor="System",
                name="context.prefix_drift",
                refs={
                    "run_id": run_id,
                    "role": role_id,
                    "task_id": getattr(request, "task_id", None),
                },
                summary=(
                    "prefix drift detected"
                    if report.drifted
                    else ("prefix first seen" if report.first_seen else "prefix stable")
                ),
                output={
                    "fingerprint": report.fingerprint,
                    "drifted": bool(report.drifted),
                    "first_seen": bool(report.first_seen),
                    "previous_fingerprint": report.previous_fingerprint,
                    "prefix_chars": int(report.prefix_chars),
                    "prefix_message_count": int(report.prefix_message_count),
                    "volatile_findings": [
                        {
                            "kind": finding.kind.value,
                            "sample": finding.sample,
                            "count": int(finding.count),
                        }
                        for finding in report.volatile_findings
                    ],
                    "role": role_id,
                },
            )
        except (OSError, ValueError) as exc:  # pragma: no cover - never break a turn
            logger.debug("context.prefix_drift emit failed: %s", exc)
