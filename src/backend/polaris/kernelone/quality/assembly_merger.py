"""Deterministic file-assembly merger (P3 — codex directive 2026-06-15).

A code file > the per-step line ceiling is built as ``skeleton + fill1..fillN``.
The skeleton is the interface LAW: the complete file shell (imports/exports,
global state, every function signature as a stub) with a ``// @anchor:<name>``
marker (``# @anchor:<name>`` for Python) inside each function body. Each fill may
ONLY implement the bodies of its OWNED anchors.

This module is the SYSTEM-level guarantee codex asked for: it turns "keep one file
coherent across N fill-turns" from a weak-model MEMORY burden into a deterministic
CHECK. Given the skeleton baseline and a fill's proposed file content, it REJECTS:

* ``assembly_interface_drift``   — a function signature / import / export changed
* ``assembly_out_of_region``     — an UNASSIGNED function body was modified
* ``assembly_missing_anchor``    — an ``@anchor`` marker was dropped (incl. whole-file rewrite)
* ``assembly_duplicate_anchor``  — an ``@anchor`` name appears more than once

Pure + language-generic (JS/TS brace, Python ``def``). No I/O. The caller (the
Director materialization path) re-asks the fill on rejection — never dead-letters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ASSEMBLY_INTERFACE_DRIFT = "assembly_interface_drift"
ASSEMBLY_OUT_OF_REGION = "assembly_out_of_region"
ASSEMBLY_MISSING_ANCHOR = "assembly_missing_anchor"
ASSEMBLY_DUPLICATE_ANCHOR = "assembly_duplicate_anchor"

_ANCHOR_RE = re.compile(r"@anchor:\s*([A-Za-z_$][\w$]*)")
# A function/method/class signature line (declaration only — body excluded). Language
# generic: JS function/const-arrow/class + Python def/class. Used for the interface
# snapshot and to segment the file into per-function bodies.
_SIGNATURE_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?"
    r"(?:function\s+|def\s+|class\s+|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>))"
)
_IMPORT_EXPORT_RE = re.compile(r"^\s*(?:import\b|export\b|from\b|require\s*\(|module\.exports\b)")


@dataclass(frozen=True)
class AssemblyVerdict:
    """Result of validating a fill's proposed content against the skeleton baseline."""

    ok: bool
    error_code: str = ""
    message: str = ""


def anchor_names(content: str) -> list[str]:
    """All ``@anchor:<name>`` names in source order (duplicates preserved)."""
    return [m.group(1) for m in _ANCHOR_RE.finditer(content or "")]


def _interface_snapshot(content: str) -> tuple[str, ...]:
    """Sorted, whitespace-normalised set of interface lines: every signature line plus
    every import/export/require line. Changing any of these is interface drift."""
    snapshot: set[str] = set()
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SIGNATURE_RE.match(raw) or _IMPORT_EXPORT_RE.match(raw):
            snapshot.add(re.sub(r"\s+", " ", line))
    return tuple(sorted(snapshot))


def _function_bodies_by_anchor(content: str) -> dict[str, str]:
    """Map each anchor name → the body text of the function that contains its marker.

    Segments the file at signature lines; each segment's body is everything after its
    signature up to the next signature (or EOF). The body's ``@anchor:<name>`` marker
    keys the segment. Whitespace-normalised so cosmetic reformatting is not flagged.
    """
    lines = (content or "").splitlines()
    sig_indices = [i for i, ln in enumerate(lines) if _SIGNATURE_RE.match(ln)]
    bodies: dict[str, str] = {}
    for pos, start in enumerate(sig_indices):
        end = sig_indices[pos + 1] if pos + 1 < len(sig_indices) else len(lines)
        body_lines = lines[start + 1 : end]
        body_text = "\n".join(body_lines)
        found = _ANCHOR_RE.search(body_text)
        if found:
            # Body with the anchor marker stripped + whitespace-normalised: compares the
            # IMPLEMENTATION only, so keeping the marker (per protocol) is not a diff.
            normalised = re.sub(r"\s+", " ", _ANCHOR_RE.sub("", body_text)).strip()
            bodies[found.group(1)] = normalised
    return bodies


def validate_fill_assembly(
    baseline: str, proposed: str, *, owned_anchors: list[str] | tuple[str, ...]
) -> AssemblyVerdict:
    """Validate a fill's ``proposed`` file content against the skeleton ``baseline``.

    ``owned_anchors`` are the anchors this fill is permitted to implement. Returns an
    OK verdict (the caller writes ``proposed``) or a rejection with one of the
    ``assembly_*`` error codes (the caller re-asks the fill, never dead-letters).
    """
    owned = {str(a).strip() for a in owned_anchors if str(a).strip()}
    proposed_anchor_list = anchor_names(proposed)

    duplicates = sorted({a for a in proposed_anchor_list if proposed_anchor_list.count(a) > 1})
    if duplicates:
        return AssemblyVerdict(False, ASSEMBLY_DUPLICATE_ANCHOR, f"duplicate @anchor markers: {duplicates}")

    base_anchors = set(anchor_names(baseline))
    missing = sorted(base_anchors - set(proposed_anchor_list))
    if missing:
        return AssemblyVerdict(
            False,
            ASSEMBLY_MISSING_ANCHOR,
            f"@anchor markers dropped (a fill must preserve every anchor; whole-file rewrites are rejected): {missing}",
        )

    if _interface_snapshot(baseline) != _interface_snapshot(proposed):
        return AssemblyVerdict(
            False,
            ASSEMBLY_INTERFACE_DRIFT,
            "a signature / import / export changed — the skeleton interface is law; only implement bodies",
        )

    base_bodies = _function_bodies_by_anchor(baseline)
    proposed_bodies = _function_bodies_by_anchor(proposed)
    for name, base_body in base_bodies.items():
        if name in owned:
            continue
        if proposed_bodies.get(name, "") != base_body:
            return AssemblyVerdict(
                False,
                ASSEMBLY_OUT_OF_REGION,
                f"modified an UNASSIGNED function body: {name!r} (this fill only owns {sorted(owned)})",
            )

    return AssemblyVerdict(True)
