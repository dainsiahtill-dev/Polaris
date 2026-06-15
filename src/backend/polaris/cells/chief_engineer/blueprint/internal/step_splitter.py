"""Deterministic right-sizing of over-budget construction steps (I3-r29).

A bounded-window weak Director (qwen3.6-27b @ 16k, ~one-turn output ≈ ~250 lines)
cannot one-shot a large file: live r29 the CE packed a 12-function ``main.js`` into a
single step; the write truncated mid-file (finish_reason=length) → unclosed brackets
→ PreWriteGuard blocked → zero materialized → dead-letter ``no_materialized_changes``.

The durable fix is NOT a bigger budget (a 500-line file would still truncate) but
**right-sizing the work**: split an over-budget from-scratch code step into

  * a SKELETON step — writes all function signatures as minimal valid stubs
    (small, parses, one-shot-safe), and
  * K FILL steps — each implements a few function bodies via anchored edit on the
    SAME file, chained linearly so the market serializes them and the file grows.

Pure, deterministic, fail-open: any uncertainty leaves the step un-split (status quo).
Reuses the existing machinery — market ``depends_on`` serialization, ``edit_on_prior``
routing to ``edit_blocks`` (R7), and R7-C anti-shrink protect the accumulated file.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    _MAX_STEP_LINES,
    _MAX_STEPS_PER_TASK,
    _target_requires_signatures,
)

_SPLIT_ENV = "KERNELONE_CE_STEP_SPLIT"
_SPLIT_DISABLED = {"off", "none", "disabled", "false", "0"}

# Over-fission suppression (batch-b1, 2026-06-15): a SOLE-WRITER single-file leaf
# triggered by signature COUNT alone (est_lines below the line trigger) is kept as
# ONE coherent whole-file step. The weak Director cannot stitch one file across N
# fill-turns — live b1 game.js/main.js were split into fill1..fillN that disagreed
# on interfaces, DRIFTed, and dead-lettered. Genuinely large files (est_lines
# trigger) and multi-writer same-file steps still split.
_SINGLE_FILE_NO_FISSION_ENV = "KERNELONE_CE_SINGLE_FILE_NO_FISSION"

# Trigger: a code file with enough body to risk a one-shot truncation. est_lines
# approaching the ≤120 gate ceiling OR a high signature count (the r29 signature:
# the CE under-reported est_lines but declared 12 functions).
_SPLIT_TRIGGER_LINES = 100
_SPLIT_TRIGGER_SIGS = 5
_SPLIT_MIN_SIGS = 4  # fewer than this fits one turn even at the ceiling

# Partition + sizing. Each fill ≈ 3 functions ≈ 60 lines (one-shot-safe). Skeleton
# stubs ≈ 3 lines/sig (signature + minimal body + close).
_SIGS_PER_FILL = 3
_STUB_LINES_PER_SIG = 3
_BODY_LINES_PER_SIG = 20
# Body-presence backstop: a fill's symbol grep also passes on the skeleton's stub, so
# a fill that touches-but-does-not-implement could resolve. Require the accumulated
# file to exceed a conservative cumulative line floor (real implemented function ≳ this
# many lines; a stub ≈ 2-3). Heuristic + conservative (errs toward NOT false-failing a
# terse-but-real implementation); the final fill's floor enforces the aggregate.
_FILL_MIN_IMPL_LINES = 6

_SYMBOL_RE = re.compile(r"(?:async\s+)?(?:function|def|class|const|let|var|fn|func)\s+([A-Za-z_$][A-Za-z0-9_$]*)")
_IDENT_RE = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)")
_SUBSTEP_FILL_RE = re.compile(r"-fill\d+$")


def _split_enabled() -> bool:
    return os.environ.get(_SPLIT_ENV, "").strip().lower() not in _SPLIT_DISABLED


def _single_file_no_fission_enabled() -> bool:
    return os.environ.get(_SINGLE_FILE_NO_FISSION_ENV, "").strip().lower() not in _SPLIT_DISABLED


def _is_sig_only_trigger(step: dict[str, Any]) -> bool:
    """True when ONLY the signature-count trigger fired (est_lines below the line
    trigger). Such a step is a many-small-functions file with a modest line count —
    one-turn-writable, so for a sole writer it is kept whole rather than over-fissioned.

    A sig-only step has ``est_lines < _SPLIT_TRIGGER_LINES (100) < _MAX_STEP_LINES (120)``,
    so suppressing its split never leaves an over-budget leaf that the step-contract
    gate would reject — it stays one valid whole-file step.
    """
    try:
        est_lines = int(step.get("est_lines") or 0)
    except (TypeError, ValueError):
        est_lines = 0
    trigger_lines = _read_positive_int_env("KERNELONE_STEP_SPLIT_TRIGGER_LINES", _SPLIT_TRIGGER_LINES)
    return est_lines < trigger_lines


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return default
        if value > 0:
            return value
    return default


def _norm_target(raw: Any) -> str:
    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def _syntax_check_cmd(target: str) -> str | None:
    """Return a per-language syntax-check command, or None when none is known.

    A None result means we cannot build a structural verify clause for a sub-step,
    so the step is NOT split (fail-open) — the existing gate handles it instead.
    Dispatch is by file suffix (language-generic, no business/target specifics).
    """
    low = target.lower()
    if low.endswith((".js", ".mjs", ".cjs", ".jsx")):
        return f"node --check {target}"
    if low.endswith(".py"):
        return f"python -m py_compile {target}"
    return None


def _signature_symbol(signature: str) -> str:
    """Extract the identifier from a CE-declared signature string (generic).

    Parses the CE's own declared signature text (not target-language source), so it
    carries no language/business assumptions — same posture as the interface ledger.
    """
    match = _SYMBOL_RE.search(signature or "")
    if match:
        return match.group(1)
    fallback = _IDENT_RE.search(signature or "")
    return fallback.group(1) if fallback else ""


def _symbol_greps(target: str, signatures: list[str]) -> str:
    clauses = []
    for signature in signatures:
        symbol = _signature_symbol(signature)
        if symbol:
            clauses.append(f'grep -q "{symbol}" {target}')
    return " && ".join(clauses)


def _build_verify(target: str, signatures: list[str], *, min_total_lines: int = 0) -> str:
    """Syntax check (structural, defeats the all-hollow gate) + per-symbol presence.

    For a FILL step ``min_total_lines`` adds a cumulative line floor so the step
    cannot resolve on the skeleton's inherited stubs — a touched-but-unimplemented
    file stays small and fails the floor (see :data:`_FILL_MIN_IMPL_LINES`).
    """
    cmd = _syntax_check_cmd(target) or ""
    greps = _symbol_greps(target, signatures)
    clauses = [c for c in (cmd, greps) if c]
    if min_total_lines > 0:
        clauses.append(f'[ "$(wc -l < {target})" -ge {min_total_lines} ]')
    return " && ".join(clauses)


def _is_substep(step: dict[str, Any]) -> bool:
    step_id = str(step.get("step_id") or "")
    return step_id.endswith("-skel") or bool(_SUBSTEP_FILL_RE.search(step_id))


def _should_split(step: dict[str, Any]) -> bool:
    target = _norm_target(step.get("target_file"))
    if not target or not _target_requires_signatures(target):
        return False
    if _syntax_check_cmd(target) is None:
        return False  # no structural checker for this language → fail-open
    signatures = [str(s).strip() for s in (step.get("signatures") or []) if str(s).strip()]
    if len(signatures) < _SPLIT_MIN_SIGS:
        return False
    try:
        est_lines = int(step.get("est_lines") or 0)
    except (TypeError, ValueError):
        est_lines = 0
    trigger_lines = _read_positive_int_env("KERNELONE_STEP_SPLIT_TRIGGER_LINES", _SPLIT_TRIGGER_LINES)
    trigger_sigs = _read_positive_int_env("KERNELONE_STEP_SPLIT_TRIGGER_SIGS", _SPLIT_TRIGGER_SIGS)
    return est_lines >= trigger_lines or len(signatures) >= trigger_sigs


def _split_one(step: dict[str, Any], *, parent_pm_task: str) -> list[dict[str, Any]]:
    base = str(step.get("step_id") or "").strip()
    target = _norm_target(step.get("target_file"))
    signatures = [str(s).strip() for s in (step.get("signatures") or []) if str(s).strip()]
    interface_names = list(step.get("interface_names") or [])
    base_depends = list(step.get("depends_on") or [])
    title = str(step.get("title") or "").strip()

    sigs_per_fill = _read_positive_int_env("KERNELONE_STEP_SPLIT_SIGS_PER_FILL", _SIGS_PER_FILL)
    groups = [signatures[i : i + sigs_per_fill] for i in range(0, len(signatures), sigs_per_fill)]
    total = len(groups)

    skeleton_id = f"{base}-skel"
    skeleton: dict[str, Any] = {
        "step_id": skeleton_id,
        "parent_pm_task": parent_pm_task,
        "target_file": target,
        "est_lines": min(_MAX_STEP_LINES, max(1, len(signatures) * _STUB_LINES_PER_SIG)),
        "signatures": list(signatures),
        "interface_names": interface_names,
        "verify": _build_verify(target, signatures),
        "depends_on": list(base_depends),
        "title": (f"{title} · skeleton" if title else "skeleton (stubs)"),
        # The gateway renders a hard "write empty stubs ONLY" directive: without it
        # the weak model tries to fully implement all signatures in one write and
        # truncates (live r30: a 13-function skeleton truncated finish_reason=length).
        "skeleton_stub_only": True,
    }
    out: list[dict[str, Any]] = [skeleton]
    prev_id = skeleton_id
    body_floor = _read_positive_int_env("KERNELONE_STEP_SPLIT_FILL_MIN_IMPL_LINES", _FILL_MIN_IMPL_LINES)
    implemented = 0
    for index, group in enumerate(groups, start=1):
        implemented += len(group)
        fill_id = f"{base}-fill{index}"
        out.append(
            {
                "step_id": fill_id,
                "parent_pm_task": parent_pm_task,
                "target_file": target,
                "est_lines": min(_MAX_STEP_LINES, max(1, len(group) * _BODY_LINES_PER_SIG)),
                "signatures": list(group),
                "interface_names": [],  # interface already frozen by the skeleton
                # Cumulative line floor proves bodies were filled, not left as stubs.
                "verify": _build_verify(target, group, min_total_lines=implemented * body_floor),
                "depends_on": [prev_id],  # linear chain: serializes same-file edits
                "edit_on_prior": True,
                "edit_on_prior_owner": skeleton_id,
                # The gateway renders a hard "implement ONLY your assigned functions via
                # edit_blocks, code-only, no whole-file rewrite" directive. Without it the
                # weak model tries to implement the whole file at once and either truncates
                # or stuffs prose into edit_blocks (live r31: fill1 dead-lettered).
                "fill_scope_only": True,
                "title": (f"{title} · fill {index}/{total}" if title else f"fill {index}/{total}"),
            }
        )
        prev_id = fill_id
    return out


def split_oversize_steps(
    steps: list[dict[str, Any]],
    *,
    parent_pm_task: str,
    owned_elsewhere: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Split over-budget from-scratch code steps into skeleton + fill chains.

    Returns the SAME list object when nothing is split (cheap identity check upstream).
    ``owned_elsewhere`` is the set of target files already owned by an EARLIER parent
    (from the file-ownership ledger): such a step is a cross-parent edit, NOT a
    from-scratch creation, so splitting it into a from-scratch skeleton would clobber
    the owner — it is left un-split (the small cross-parent edit fits the budget).
    Fail-open everywhere: env-disabled, already-split input, or a projected
    step-count overflow all return the input unchanged.
    """
    if not _split_enabled() or not steps:
        return steps
    if any(_is_substep(step) for step in steps):
        return steps  # idempotency: never re-split produced sub-steps
    owned = {_norm_target(target) for target in owned_elsewhere}
    # Multiplicity of each target across this parent's steps — a target written by
    # exactly one step is a "sole-writer" leaf (over-fission suppression below).
    target_multiplicity = Counter(norm for step in steps if (norm := _norm_target(step.get("target_file"))))
    suppress_single_file = _single_file_no_fission_enabled()

    out: list[dict[str, Any]] = []
    remap: dict[str, str] = {}  # split original step_id -> its terminal fill id
    changed = False
    for step in steps:
        step_id = str(step.get("step_id") or "")
        target = _norm_target(step.get("target_file"))
        if target not in owned and _should_split(step):
            # Over-fission suppression: a sole-writer single-file leaf triggered by
            # signature count alone is kept as ONE coherent whole-file step (the weak
            # Director cannot stitch one file across N fill-turns). Large files
            # (est_lines trigger) and multi-writer same-file steps still split.
            if suppress_single_file and target_multiplicity.get(target, 0) <= 1 and _is_sig_only_trigger(step):
                out.append(dict(step))
                continue
            sub_steps = _split_one(step, parent_pm_task=parent_pm_task)
            out.extend(sub_steps)
            remap[step_id] = sub_steps[-1]["step_id"]
            changed = True
        else:
            out.append(dict(step))  # copy so depends_on rewrite never mutates input

    if not changed:
        return steps
    if len(out) > _MAX_STEPS_PER_TASK:
        return steps  # fail-open: splitting would breach the step ceiling

    # Re-point any dependent of a split step to that split's terminal (completion) fill.
    for step in out:
        deps = step.get("depends_on") or []
        if any(dep in remap for dep in deps):
            step["depends_on"] = [remap.get(dep, dep) for dep in deps]
    return out


__all__ = ["split_oversize_steps"]
