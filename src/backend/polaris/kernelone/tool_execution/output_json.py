from __future__ import annotations

import json
from typing import Any


def parse_json_stdout(stdout: str) -> tuple[Any | None, str | None]:
    """Parse JSON payload from tool stdout, tolerating extra log lines."""
    text = str(stdout or "").strip()
    if not text:
        return {}, None

    direct_error: str | None = None
    try:
        return json.loads(text), None
    except (RuntimeError, ValueError) as exc:
        direct_error = str(exc)

    decoder = json.JSONDecoder()
    best_payload: Any | None = None
    best_rank: tuple[int, int, int, int] | None = None

    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            payload, consumed = decoder.raw_decode(text[idx:])
        except (RuntimeError, ValueError):
            continue

        leading = text[:idx].strip()
        trailing = text[idx + consumed :].strip()
        has_contract_shape = isinstance(payload, dict) and any(
            key in payload for key in ("ok", "tool", "error", "exit_code", "changed_files")
        )
        rank = (
            1 if has_contract_shape else 0,
            1 if not trailing else 0,
            1 if not leading else 0,
            idx,
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_payload = payload

    if best_payload is not None:
        return best_payload, None
    return None, direct_error or "invalid_json_output"
