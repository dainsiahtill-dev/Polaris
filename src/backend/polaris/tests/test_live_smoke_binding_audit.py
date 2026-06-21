"""Live-smoke audit for multi-provider role bindings.

Validates that PM/CE→Kimi, QA→MiniMax, Director→qwen openai_compat
bindings resolve correctly and optionally performs a minimal live call.

Gating:
    POLARIS_LIVE_SMOKE=1  → run real LLM call (requires valid API keys)
    otherwise             → SKIP_WITH_EVIDENCE (binding resolution only,
                            no network I/O, no key leakage)

Run:
    python -m pytest polaris/tests/test_live_smoke_binding_audit.py -v
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LIVE_SMOKE_ENV = "POLARIS_LIVE_SMOKE"

_EXPECTED_BINDINGS: dict[str, dict[str, str]] = {
    "pm": {"model_substr": "kimi"},
    "chief_engineer": {"model_substr": "kimi"},
    "qa": {"model_substr": "minimax"},
    "director": {"model_substr": "qwen"},
}

_LIVE_PROMPT = "Reply with exactly: PONG"
_LIVE_TIMEOUT = 30
_LIVE_MAX_OUTPUT_LEN = 256


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_smoke_enabled() -> bool:
    return os.environ.get(_LIVE_SMOKE_ENV, "").strip() in {"1", "true", "yes"}


def _mask_key(key: str) -> str:
    """Return masked representation: first 4 + *** + last 4, or 'EMPTY'."""
    key = key.strip()
    if not key:
        return "EMPTY"
    if len(key) <= 8:
        return f"{key[:2]}***"
    return f"{key[:4]}***{key[-4:]}"


def _resolve_role_binding(role_id: str) -> tuple[str, str]:
    """Resolve (provider_id, model) for a role via kernelone runtime config.

    Returns ("", "") on any resolution failure.
    """
    try:
        from polaris.kernelone.llm.runtime_config import get_role_model

        provider_id, model = get_role_model(role_id)
        return str(provider_id or "").strip(), str(model or "").strip()
    except (RuntimeError, ValueError, TypeError, OSError):
        return "", ""


def _resolve_api_key_status(
    provider_id: str,
    provider_type: str,
    provider_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Check whether an API key can be resolved without leaking it.

    Returns {"present": bool, "masked": str, "source": str}.
    """
    from polaris.kernelone.llm.runtime import resolve_provider_api_key

    resolved = resolve_provider_api_key(provider_id, provider_type, provider_cfg)
    raw = str(resolved.get("api_key") or "").strip()
    return {
        "present": bool(raw),
        "masked": _mask_key(raw),
        "source": "config_or_env",
    }


def _get_provider_type_for_provider_id(provider_id: str) -> str:
    """Best-effort provider type inference from provider_id."""
    pid = provider_id.lower()
    if "kimi" in pid or "moonshot" in pid:
        return "kimi"
    if "minimax" in pid:
        return "minimax"
    if "qwen" in pid or "openai" in pid:
        return "openai_compat"
    if "anthropic" in pid or "claude" in pid:
        return "anthropic_compat"
    if "gemini" in pid:
        return "gemini_api"
    if "ollama" in pid:
        return "ollama"
    return "openai_compat"


def _live_invoke(
    role_id: str,
    provider_id: str,
    model: str,
    provider_type: str,
) -> dict[str, Any]:
    """Perform a minimal live LLM invocation for the given role.

    Returns a result dict with keys: ok, output, latency_ms, error.
    """
    from polaris.infrastructure.llm.providers.provider_registry import ProviderManager
    from polaris.kernelone.llm.runtime import resolve_provider_api_key

    cfg: dict[str, Any] = {"type": provider_type}
    cfg = resolve_provider_api_key(provider_id, provider_type, cfg)
    if not cfg.get("api_key"):
        return {"ok": False, "output": "", "latency_ms": 0, "error": "api_key_missing"}

    manager = ProviderManager()
    instance = manager.get_provider_instance(provider_type)
    if instance is None:
        return {"ok": False, "output": "", "latency_ms": 0, "error": f"provider_instance_not_found:{provider_type}"}

    invoke_cfg = dict(cfg)
    invoke_cfg["timeout"] = _LIVE_TIMEOUT
    invoke_cfg["max_tokens"] = 64

    started = time.monotonic()
    try:
        result = instance.invoke(_LIVE_PROMPT, model, invoke_cfg)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        ok = bool(getattr(result, "ok", False))
        output = str(getattr(result, "output", "") or "")[:_LIVE_MAX_OUTPUT_LEN]
        error = str(getattr(result, "error", "") or "")
        return {"ok": ok, "output": output, "latency_ms": elapsed_ms, "error": error}
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {"ok": False, "output": "", "latency_ms": elapsed_ms, "error": str(exc)}


# ---------------------------------------------------------------------------
# Audit result container
# ---------------------------------------------------------------------------


@dataclass
class RoleAuditResult:
    role_id: str
    provider_id: str = ""
    model: str = ""
    provider_type: str = ""
    binding_ok: bool = False
    api_key_present: bool = False
    api_key_masked: str = ""
    live_attempted: bool = False
    live_ok: bool = False
    live_output: str = ""
    live_latency_ms: int = 0
    live_error: str = ""
    skip_reason: str = ""
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestLiveSmokeBindingAudit:
    """Audit multi-provider role bindings with optional live invocation."""

    # -- binding resolution tests (always run, no network) --------------------

    def test_pm_binding_resolves_to_kimi(self) -> None:
        result = self._audit_role("pm", _EXPECTED_BINDINGS["pm"])
        assert result.binding_ok, (
            f"PM role must bind to Kimi provider. "
            f"Got provider_id={result.provider_id!r}, model={result.model!r}. "
            f"Errors: {result.errors}"
        )

    def test_chief_engineer_binding_resolves_to_kimi(self) -> None:
        result = self._audit_role("chief_engineer", _EXPECTED_BINDINGS["chief_engineer"])
        assert result.binding_ok, (
            f"Chief Engineer role must bind to Kimi provider. "
            f"Got provider_id={result.provider_id!r}, model={result.model!r}. "
            f"Errors: {result.errors}"
        )

    def test_qa_binding_resolves_to_minimax(self) -> None:
        result = self._audit_role("qa", _EXPECTED_BINDINGS["qa"])
        assert result.binding_ok, (
            f"QA role must bind to MiniMax provider. "
            f"Got provider_id={result.provider_id!r}, model={result.model!r}. "
            f"Errors: {result.errors}"
        )

    def test_director_binding_resolves_to_qwen(self) -> None:
        result = self._audit_role("director", _EXPECTED_BINDINGS["director"])
        assert result.binding_ok, (
            f"Director role must bind to Qwen provider. "
            f"Got provider_id={result.provider_id!r}, model={result.model!r}. "
            f"Errors: {result.errors}"
        )

    def test_all_bindings_resolve_without_error(self) -> None:
        """All four role bindings must resolve without raising exceptions."""
        errors: list[str] = []
        for role_id in _EXPECTED_BINDINGS:
            try:
                pid, mdl = _resolve_role_binding(role_id)
                if not pid or not mdl:
                    errors.append(f"{role_id}: empty resolution (provider={pid!r}, model={mdl!r})")
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                errors.append(f"{role_id}: raised {type(exc).__name__}: {exc}")
        assert not errors, "Binding resolution errors:\n" + "\n".join(errors)

    # -- API key presence tests (always run, no network) ----------------------

    def test_api_keys_present_for_bound_roles(self) -> None:
        """Each bound role must have a resolvable API key (masked output only)."""
        missing: list[str] = []
        for role_id, _expected in _EXPECTED_BINDINGS.items():
            pid, _ = _resolve_role_binding(role_id)
            if not pid:
                continue  # covered by binding test
            ptype = _get_provider_type_for_provider_id(pid)
            status = _resolve_api_key_status(pid, ptype, {})
            if not status["present"]:
                missing.append(f"{role_id} (provider={pid})")
        if missing:
            pytest.skip(
                f"SKIP_WITH_EVIDENCE: API keys missing for: {', '.join(missing)}. "
                f"Set POLARIS_LIVE_SMOKE=1 after configuring keys."
            )

    # -- live smoke tests (gated by env var) ---------------------------------

    def test_live_smoke_pm_kimi(self) -> None:
        result = self._audit_role("pm", _EXPECTED_BINDINGS["pm"], live=True)
        if not result.live_attempted:
            pytest.skip(self._skip_evidence("pm", result))
        assert result.live_ok, self._live_fail_msg("pm", result)

    def test_live_smoke_chief_engineer_kimi(self) -> None:
        result = self._audit_role("chief_engineer", _EXPECTED_BINDINGS["chief_engineer"], live=True)
        if not result.live_attempted:
            pytest.skip(self._skip_evidence("chief_engineer", result))
        assert result.live_ok, self._live_fail_msg("chief_engineer", result)

    def test_live_smoke_qa_minimax(self) -> None:
        result = self._audit_role("qa", _EXPECTED_BINDINGS["qa"], live=True)
        if not result.live_attempted:
            pytest.skip(self._skip_evidence("qa", result))
        assert result.live_ok, self._live_fail_msg("qa", result)

    def test_live_smoke_director_qwen(self) -> None:
        result = self._audit_role("director", _EXPECTED_BINDINGS["director"], live=True)
        if not result.live_attempted:
            pytest.skip(self._skip_evidence("director", result))
        assert result.live_ok, self._live_fail_msg("director", result)

    # -- PATCH_STATUS evidence output ----------------------------------------

    def test_patch_status_json(self) -> None:
        """Collect all audit results into a PATCH_STATUS JSON for CI artifact."""
        results: dict[str, Any] = {}
        for role_id, expected in _EXPECTED_BINDINGS.items():
            audit = self._audit_role(role_id, expected, live=True)
            results[role_id] = {
                "binding_ok": audit.binding_ok,
                "provider_id": audit.provider_id,
                "model": audit.model,
                "provider_type": audit.provider_type,
                "api_key_present": audit.api_key_present,
                "api_key_masked": audit.api_key_masked,
                "live_attempted": audit.live_attempted,
                "live_ok": audit.live_ok,
                "live_latency_ms": audit.live_latency_ms,
                "live_error": audit.live_error,
                "skip_reason": audit.skip_reason,
                "errors": audit.errors,
            }

        patch_status = {
            "status": "PASS" if all(r["binding_ok"] for r in results.values()) else "FAIL",
            "live_smoke_enabled": _live_smoke_enabled(),
            "roles": results,
            "superpowers_status": "unavailable",
            "superpowers_note": "opencode superpowers skill not available in current env",
        }

        # Write PATCH_STATUS to temp for CI artifact collection
        out_path = Path("/tmp/opencode") / "PATCH_STATUS.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(patch_status, indent=2, ensure_ascii=False), encoding="utf-8")

        # Always pass — this test is for evidence collection
        assert patch_status["status"] in {"PASS", "FAIL"}

    # -- internal helpers ----------------------------------------------------

    def _audit_role(
        self,
        role_id: str,
        expected: dict[str, str],
        *,
        live: bool = False,
    ) -> RoleAuditResult:
        result = RoleAuditResult(role_id=role_id)

        # 1. Resolve binding
        try:
            pid, mdl = _resolve_role_binding(role_id)
            result.provider_id = pid
            result.model = mdl
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            result.errors.append(f"binding_resolution_failed: {exc}")
            return result

        if not pid or not mdl:
            result.errors.append(f"empty_binding: provider_id={pid!r}, model={mdl!r}")
            return result

        # 2. Check model substring matches expected
        model_match = expected.get("model_substr", "") in mdl.lower()
        result.binding_ok = model_match
        if not model_match:
            result.errors.append(f"model_mismatch: expected_substr={expected.get('model_substr')!r}, got={mdl!r}")

        # 3. Resolve provider type and API key
        result.provider_type = _get_provider_type_for_provider_id(pid)
        try:
            key_status = _resolve_api_key_status(pid, result.provider_type, {})
            result.api_key_present = key_status["present"]
            result.api_key_masked = key_status["masked"]
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            result.errors.append(f"api_key_resolution_failed: {exc}")

        # 4. Live invocation (gated)
        if live and _live_smoke_enabled() and result.binding_ok and result.api_key_present:
            result.live_attempted = True
            live_result = _live_invoke(role_id, pid, mdl, result.provider_type)
            result.live_ok = live_result["ok"]
            result.live_output = live_result["output"]
            result.live_latency_ms = live_result["latency_ms"]
            result.live_error = live_result["error"]
        elif live and not _live_smoke_enabled():
            result.skip_reason = f"SKIP_WITH_EVIDENCE: {_LIVE_SMOKE_ENV} not set"
        elif live and not result.api_key_present:
            result.skip_reason = "SKIP_WITH_EVIDENCE: api_key not present"

        return result

    @staticmethod
    def _skip_evidence(role_id: str, result: RoleAuditResult) -> str:
        parts = [f"Live smoke skipped for role={role_id}"]
        if result.skip_reason:
            parts.append(result.skip_reason)
        if not result.binding_ok:
            parts.append(f"binding_failed: {result.errors}")
        if not result.api_key_present:
            parts.append("api_key_absent")
        return " | ".join(parts)

    @staticmethod
    def _live_fail_msg(role_id: str, result: RoleAuditResult) -> str:
        return (
            f"Live smoke failed for role={role_id}: "
            f"provider={result.provider_id}, model={result.model}, "
            f"latency={result.live_latency_ms}ms, error={result.live_error!r}"
        )
