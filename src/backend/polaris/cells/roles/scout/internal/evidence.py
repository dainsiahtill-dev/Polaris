"""Build a tamper-evident content hash for a scout report via EvidencePackage (UTF-8).

Note: EvidencePackage.compute_hash() includes a timestamp, so we compute a
deterministic hash from the stable content fields directly.
"""
from __future__ import annotations

import hashlib
import json

from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.domain.verification.evidence_collector import EvidenceCollector

_HASH_LEN = 16


def build_content_hash(
    *,
    task_id: str,
    findings: list[ScoutFinding],
    summary: str,
    tools_used: list[str],
) -> str:
    """Record findings as evidence entries, return a stable content hash.

    EvidenceCollector is used to normalise the entries; the content hash is
    derived from the stable fields (tools, findings, summary) rather than the
    full package dict (which includes a wall-clock timestamp).
    """
    collector = EvidenceCollector(task_id=task_id or "scout-probe", iteration=0)
    for tool in tools_used:
        collector.record_tool_execution(tool_name=tool, command=tool, exit_code=0)
    for f in findings:
        collector.record_audit_entry({
            "kind": "scout_finding",
            "path": f.path,
            "line": f.line,
            "symbol": f.symbol,
            "confidence": f.confidence,
        })
    collector.set_summary(summary, acceptance=None)
    pkg = collector.get_package()
    # Stable content: omit created_at / recorded_at timestamps
    stable = {
        "task_id": pkg.task_id,
        "tools": [t.tool_name for t in pkg.tool_outputs],
        "audit_entries": [
            {k: v for k, v in e.items() if k != "recorded_at"}
            for e in pkg.audit_entries
        ],
        "summary": pkg.summary,
    }
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:_HASH_LEN]
