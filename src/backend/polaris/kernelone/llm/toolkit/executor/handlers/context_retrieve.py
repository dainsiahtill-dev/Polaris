"""Context retrieve tool handler — reversible CCR loop-close.

Provides the model-callable ``context_retrieve`` tool. ContextOS pointerizes
large payloads and injects reference markers (``<<ref:HASH>>``,
``[receipt_ref:ID]``, ``<receipt_ref:ID>``, ``[See path]``). This handler is the
reverse channel: given such a reference it returns the ORIGINAL payload when it
is still in the TTL-bounded CCR cache, or — failing that — best-effort receipt
metadata from the infra ``SessionReceiptStore``.

Fail-closed: an unknown / expired / unparseable ref returns ``ok=False`` with a
clear ``not_retrievable`` reason. It never silently reports success.

Weak-model ergonomics: the canonical argument is ``ref``. The SchemaDriven
normalizer maps ``hash``/``id``/``pointer``/``receipt_ref`` -> ``ref`` from the
spec ``arg_aliases`` (no teaching error). As a defensive fallback (in case the
spec has not been loaded into the registry yet) this handler also reads those
aliases inline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.original_payload_cache import (
    get_default_cache,
    strip_ref_markers,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

logger = logging.getLogger(__name__)

# Inline alias fallback. The authoritative alias mapping lives in the tool spec
# ``arg_aliases`` and is applied by the SchemaDriven normalizer before dispatch.
# These are only consulted when the canonical ``ref`` is absent — a belt-and-
# suspenders guard against import-ordering gaps, NOT a second source of truth.
_REF_ARG_ALIASES: tuple[str, ...] = ("ref", "hash", "id", "pointer", "receipt_ref")

# Candidate locations (relative to ``<workspace>/runtime``) for the canonical
# receipt sqlite DB. Mirrors the on-disk layout used by
# polaris/infrastructure/accel/query/project_stats.py (``<state>/session_receipts.db``).
_RECEIPT_DB_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("state", "session_receipts.db"),
    ("session_receipts.db",),
)


# Tool spec for ``context_retrieve``. The DURABLE home for this is
# ``_BUILTIN_REGISTRY`` in tool_spec_registry.py (so it is present before the
# SchemaDriven normalizer caches ``get_all_specs()`` and before the executor's
# spec lookup). That file is owned by another scope, so this module ALSO
# registers the spec idempotently at import time as an owned-only fallback —
# see ``ensure_context_retrieve_spec_registered``.
_CONTEXT_RETRIEVE_SPEC: dict[str, Any] = {
    "category": "read",
    "description": (
        "Retrieve the ORIGINAL content behind a compression/receipt pointer "
        "(e.g. <<ref:HASH>>, [receipt_ref:ID], <receipt_ref:ID>). Use this to "
        "recover context that was pointerized away during compaction."
    ),
    "aliases": ["expand_pointer", "expand_receipt", "fetch_receipt", "retrieve_original"],
    "arg_aliases": {"hash": "ref", "id": "ref", "pointer": "ref", "receipt_ref": "ref"},
    "arguments": [{"name": "ref", "type": "string", "required": True}],
    "response_format_hint": "Original payload (if cached) or receipt metadata (best-effort).",
    "required_any": [("ref",)],
    "required_doc": "args.ref required",
    "handler_module": "polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve",
    "handler_function": "_handle_context_retrieve",
}


def ensure_context_retrieve_spec_registered() -> None:
    """Idempotently register the context_retrieve spec into ToolSpecRegistry.

    Owned-only fallback for the durable ``_BUILTIN_REGISTRY`` entry. Safe to call
    repeatedly: ToolSpecRegistry.register is non-strict and returns early when the
    canonical name is already present.
    """
    try:
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        if not ToolSpecRegistry.is_registered("context_retrieve"):
            ToolSpecRegistry.register("context_retrieve", dict(_CONTEXT_RETRIEVE_SPEC))
    except (ImportError, RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
        logger.debug("[context_retrieve] spec registration skipped: %s", exc)


# Register at import so the spec is available wherever this handler module is
# imported (e.g. via ToolHandlerRegistry.load_all()).
ensure_context_retrieve_spec_registered()


def register_handlers() -> dict[str, Any]:
    """Return the context-retrieve handler mapping for the executor registry."""
    ensure_context_retrieve_spec_registered()
    return {"context_retrieve": _handle_context_retrieve}


def _extract_ref(kwargs: dict[str, Any]) -> str:
    """Pull the reference out of kwargs, tolerating weak-model arg aliases."""
    for key in _REF_ARG_ALIASES:
        value = kwargs.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _resolve_receipt_db_path(workspace: str) -> Path | None:
    """Locate the canonical receipt sqlite DB for ``workspace`` if it exists.

    Resolves directly against ``<workspace>/runtime`` to match the on-disk layout
    used elsewhere (project_stats), independent of ramdisk/storage-root config.
    """
    runtime_root = Path(workspace) / "runtime"
    for relative in _RECEIPT_DB_CANDIDATES:
        candidate = runtime_root.joinpath(*relative)
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _lookup_receipt_metadata(workspace: str, ref: str) -> dict[str, Any] | None:
    """Best-effort: treat ``ref`` as a receipt job_id and return its metadata.

    Returns the receipt row payload (``result_ref`` pointer, ``status``,
    ``changed_files``, ``tool`` ...) or None. This is metadata only — the receipt
    store does not retain the original payload, so this cannot reconstruct the
    pointerized bytes.
    """
    db_path = _resolve_receipt_db_path(workspace)
    if db_path is None:
        return None
    try:
        from polaris.infrastructure.db.repositories.accel_session_receipt_store import (
            SessionReceiptStore,
        )

        store = SessionReceiptStore(db_path)
        return store.get_receipt(job_id=ref)
    except (RuntimeError, ValueError, OSError) as exc:
        logger.debug("[context_retrieve] receipt lookup failed for ref=%s: %s", ref, exc)
        return None


def _handle_context_retrieve(self: AgentAccelToolExecutor, **kwargs: Any) -> dict[str, Any]:
    """Resolve a compression/receipt pointer back to its original payload.

    Resolution order:
      1. CCR original-payload cache (the only fully reversible channel).
      2. Receipt-store metadata (best-effort; metadata only, no original bytes).
      3. Fail-closed: ``ok=False`` with a ``not_retrievable`` reason.
    """
    raw_ref = _extract_ref(kwargs)
    if not raw_ref:
        return {
            "ok": False,
            "error": "context_retrieve requires a 'ref' (a pointer/hash such as <<ref:HASH>> or [receipt_ref:ID])",
            "error_type": "missing_ref",
        }

    ref = strip_ref_markers(raw_ref)
    if not ref:
        return {
            "ok": False,
            "error": f"Could not parse a reference out of {raw_ref!r}",
            "error_type": "unparseable_ref",
        }

    # 1) CCR reversible cache — returns the actual original content.
    original = get_default_cache().get(ref)
    if original is not None:
        return {
            "ok": True,
            "ref": ref,
            "source": "ccr_cache",
            "content": original,
            "chars": len(original),
        }

    # 2) Best-effort receipt metadata (no original payload available there).
    workspace = str(getattr(self, "workspace", "") or "").strip()
    if workspace:
        receipt = _lookup_receipt_metadata(workspace, ref)
        if receipt is not None:
            return {
                "ok": True,
                "ref": ref,
                "source": "receipt_store",
                "receipt": receipt,
                "note": (
                    "Receipt metadata only — the original payload was not cached "
                    "(it may have expired from the CCR cache or was never stored)."
                ),
            }

    # 3) Fail-closed: nothing retrievable for this ref.
    return {
        "ok": False,
        "error": (
            f"No retrievable content for ref {ref!r}. It is not in the CCR cache "
            "(likely expired past its TTL) and is not a known receipt id."
        ),
        "error_type": "not_retrievable",
        "ref": ref,
    }
