"""The bounded native working set keeps its visual evidence projectable.

The native tool working set is bounded before the request is built, because
it has no budget of its own. The bound drops whole older turns, and the
visual projection then has to run on the bounded history exactly once: the
projection moves the image bytes out of the tool content into attachment
blocks, so a history that was projected before the compression would be
projected twice and would read image blocks without their bytes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import _bounded_request_messages
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand
from polaris.cells.roles.runtime.tests.test_engineering_resource_state import create


def _jpeg(width: int = 64, height: int = 48) -> bytes:
    from PIL import Image

    raw = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(raw, format="JPEG", quality=70)
    return raw.getvalue()


def _browser_observation() -> dict[str, Any]:
    data = _jpeg()
    return {
        "media_type": "image/jpeg",
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": 64,
        "height": 48,
        "label": "frame 1",
    }


def _tool_message(call_id: str, images: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(
            {
                "call_id": call_id,
                "tool": "browser_observe",
                "ok": True,
                "content": "{}",
                "images": images,
            },
            ensure_ascii=False,
        ),
    }


def _assistant_turn(index: int) -> list[dict[str, Any]]:
    call_id = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": f"turn {index}",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": json.dumps({"command": "x" * 200})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({"tool": "execute_command", "ok": True, "content": "y" * 8000}),
        },
    ]


@pytest.mark.asyncio
async def test_bounded_working_set_keeps_the_visual_evidence_projectable(tmp_path: Path) -> None:
    """The bound must not leave image blocks without their bytes."""
    workspace = tmp_path / "bounded-working-set"
    store = create(workspace)
    command = RoleRunCommand("run-1", "director", "Deliver the artifact.", workspace)

    images = [_browser_observation()]
    messages: list[dict[str, Any]] = [{"role": "user", "content": "Deliver the artifact."}]
    for index in range(80):
        messages.extend(_assistant_turn(index))
    messages.append(_tool_message("call-browser", images))

    # The browser observation is a durable tool observation.
    store.append_event(
        "run-1",
        kind="tool_observed",
        role="director",
        payload={
            "call_id": "call-browser",
            "tool": "browser_observe",
            "ok": True,
            "turn_seq": 80,
            "images": images,
        },
    )

    audit: dict[str, Any] = {}
    bounded = _bounded_request_messages(
        messages,
        store=store,
        command=command,
        role="director",
        context_window_tokens=128_000,
        visual_binding=None,
        audit=audit,
    )

    assert bounded, "the bounded request must keep a working set"
    assert len(bounded) < len(messages)
    # The visual evidence is still projectable: the image bytes survived.
    image_sets = audit.get("image_sets") or []
    assert image_sets, audit
    blocks = [block for message in bounded if isinstance(message.get("content"), list) for block in message["content"]]
    assert any(isinstance(block, dict) and block.get("type") == "image" for block in blocks)
