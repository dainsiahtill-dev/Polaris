"""Shadow projection of operator-pinned verification failures from the engineering journal.

The host counterexample_refs field accepts a JSON list of source
session_id/event_seq pairs. Empty configuration disables selection. A hidden
evaluation can keep a fixed reference set (or none) across all subjects.
Current attempts never enroll themselves as training or reference evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore


def pinned_counterexamples(refs: tuple[dict[str, Any], ...]) -> tuple[tuple[str, int], ...]:
    pins: list[tuple[str, int]] = []
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("session_id"), str)
            or not ref["session_id"]
            or type(ref.get("event_seq")) is not int
            or ref["event_seq"] < 1
        ):
            raise ValueError("Each counterexample requires a source session_id and positive integer event_seq")
        pins.append((ref["session_id"], ref["event_seq"]))
    return tuple(dict.fromkeys(pins))


def project_counterexamples(
    store: EngineeringSessionStore, target_id: str, pins: tuple[tuple[str, int], ...]
) -> dict[str, Any]:
    selected = []
    unavailable = []
    for source_id, seq in pins:
        ref = {"session_id": source_id, "event_seq": seq}
        failure = store.comparable_failed_verification(target_id, source_id, seq)
        if failure is None:
            unavailable.append(ref)
            continue
        binding = failure["input_binding"]
        checks = [
            {
                "command": command,
                "call_id": check["call_id"],
                "exit_code": check.get("exit_code"),
                "timed_out": bool(check.get("timed_out")),
                "output_sha256": hashlib.sha256(check["content"].encode("utf-8")).hexdigest(),
            }
            for command, check in zip(binding["commands"], failure["checks"], strict=True)
            if check.get("ok") is not True or check.get("exit_code") != 0 or check.get("timed_out")
        ]
        selected.append(
            {
                "source": ref,
                "source_revision": failure["workspace_revision"],
                "verification_attempt_seq": binding.get("attempt_seq"),
                "source_binding_sha256": hashlib.sha256(
                    json.dumps(binding, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "failed_checks": checks,
            }
        )
    return {"mode": "shadow", "counterexamples": selected, "unmatched_or_unverified_refs": unavailable}
