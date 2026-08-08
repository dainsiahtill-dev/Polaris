"""Round B-v3: TypeScript typecheck detail must preserve TS error codes across lines.

L1-01 m03-r23 root cause: ``_scan_typescript_project_typecheck_evidence`` captured
only the FIRST non-empty tsc line (truncated to 400 chars), stripping the TS error
codes (TS2584/TS2304) the repair coverage needs to match the ``tsconfig_lib`` /
``tsconfig_dom_html_globals`` rules. With only a generic
``typescript_project_typecheck_failed`` code reaching coverage, no DOM-lib repair
fired, the tsconfig kept ``lib:['ES2020']`` (missing DOM), and ~19000 TS2304/TS2584
errors killed the build. The detail helper must capture enough tsc output to
preserve TS codes so the existing DOM-lib repair can fire.
"""

from __future__ import annotations

from polaris.kernelone.quality.artifact_quality import _typescript_typecheck_diagnostic_detail


def test_typescript_typecheck_detail_preserves_ts_codes_across_lines() -> None:
    raw = (
        "src/web.ts(48,14): error TS2584: Cannot find name 'document'. "
        "Try changing the 'lib' compiler option to include 'dom'.\n"
        "src/engine/renderer.ts(53,21): error TS2304: Cannot find name 'HTMLCanvasElement'.\n"
        "src/web.ts(114,16): error TS2304: Cannot find name 'window'.\n"
    )
    detail = _typescript_typecheck_diagnostic_detail(raw, returncode=2)

    # Multiple TS codes preserved (not just the first line).
    assert "TS2584" in detail
    assert "TS2304" in detail
    # DOM globals preserved so the dom-html/window rules' raw_terms can match.
    assert "HTMLCanvasElement" in detail
    assert "window" in detail
    assert "include 'dom'" in detail


def test_typescript_typecheck_detail_fallback_when_output_empty() -> None:
    detail = _typescript_typecheck_diagnostic_detail("", returncode=2)
    assert "tsc --noEmit" in detail
    assert "2" in detail
