"""Visual value/payload compatibility; no model or target delivery evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts import engineering_run as contracts

PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")


def frame(**changes: Any) -> contracts.ImageObservation:
    data: dict[str, Any] = {
        "media_type": "image/png",
        "data_base64": base64.b64encode(PIXEL).decode("ascii"),
        "width": 1,
        "height": 1,
        "sha256": hashlib.sha256(PIXEL).hexdigest(),
        "label": "actual frame",
    }
    data.update(changes)
    return contracts.ImageObservation(**data)


def test_real_image_bytes_survive_payload_json_roundtrip() -> None:
    image = frame()
    observation = contracts.ToolObservation("capture", "browser_observe", True, "metadata", 0, False, images=(image,))
    payload = contracts.tool_observation_payload(observation)
    assert json.loads(json.dumps(payload))["images"][0] == asdict(image)
    assert base64.b64decode(payload["images"][0]["data_base64"], validate=True) == PIXEL


def test_nonvisual_payload_keeps_original_shape() -> None:
    observation = contracts.ToolObservation("read", "read_file", True, "text", None, False)
    assert contracts.tool_observation_payload(observation) == {
        "call_id": "read",
        "tool": "read_file",
        "ok": True,
        "content": "text",
        "exit_code": None,
        "interrupted": False,
        "timed_out": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "0" * 64},
        {"data_base64": "not base64"},
        {"width": True},
        {"height": 0},
        {"media_type": "text/html"},
        {"label": ""},
    ],
)
def test_malformed_image_value_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        frame(**change)


def test_browser_config_default_off_and_explicit_host_value() -> None:
    original = contracts.PermissionScope((Path("/workspace"),), (), frozenset(), False)
    assert original.browser is None
    browser = contracts.BrowserObservationConfig("/readonly/chrome", "/usr/bin/python3", ("/readonly/packages",), True)
    selected = contracts.PermissionScope(
        original.read_roots, (), frozenset({"browser_observe"}), False, browser=browser
    )
    payload = asdict(selected.browser)
    assert payload["require_visual_review"] is True
    assert payload["executable"] == "/readonly/chrome"
    assert contracts.BrowserObservationConfig(**payload) == browser
