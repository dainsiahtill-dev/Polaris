"""Read-only ContextOS projection replay CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any


async def replay_contextos_messages(
    messages: list[dict[str, Any]],
    *,
    workspace: str = ".",
    focus: str = "",
    recent_window_messages: int = 8,
) -> dict[str, Any]:
    """Replay messages through the ContextOS projection pipeline."""

    from polaris.kernelone.context.context_os.runtime import StateFirstContextOS

    engine = StateFirstContextOS(workspace=workspace)
    try:
        projection = await engine.project(
            messages=messages,
            focus=focus,
            recent_window_messages=recent_window_messages,
        )
        report = engine.get_last_projection_report() or {}
        return {
            "ok": True,
            "workspace": workspace,
            "projection": {
                "head_anchor": projection.head_anchor,
                "tail_anchor": projection.tail_anchor,
                "active_window_count": len(projection.active_window),
                "snapshot_event_count": len(projection.snapshot.transcript_log),
                "artifact_stub_count": len(projection.artifact_stubs),
                "episode_card_count": len(projection.episode_cards),
            },
            "projection_report": report,
        }
    finally:
        await engine.cleanup()


def load_messages_from_json(value: str) -> list[dict[str, Any]]:
    """Load ContextOS replay messages from a JSON string."""

    payload = json.loads(value)
    messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(messages, list):
        raise ValueError("messages payload must be a list or an object with a messages list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise ValueError(f"message at index {index} must be an object")
        normalized.append(dict(item))
    return normalized


def load_messages_from_file(path: str) -> list[dict[str, Any]]:
    """Load ContextOS replay messages from a UTF-8 JSON file."""

    return load_messages_from_json(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay messages through ContextOS projection.")
    parser.add_argument("--workspace", default=".", help="Workspace path used for ContextOS diagnostics.")
    parser.add_argument("--focus", default="", help="Optional projection focus text.")
    parser.add_argument("--recent-window-messages", type=int, default=8)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--messages-json", help="JSON list or {'messages': [...]} payload.")
    source.add_argument("--messages-file", help="UTF-8 JSON file containing replay messages.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    messages = (
        load_messages_from_json(args.messages_json)
        if args.messages_json is not None
        else load_messages_from_file(args.messages_file)
    )
    with contextlib.redirect_stdout(sys.stderr):
        result = asyncio.run(
            replay_contextos_messages(
                messages,
                workspace=str(args.workspace),
                focus=str(args.focus),
                recent_window_messages=int(args.recent_window_messages),
            )
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_parser",
    "load_messages_from_file",
    "load_messages_from_json",
    "main",
    "replay_contextos_messages",
]
