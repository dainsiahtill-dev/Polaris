"""Bounded artifact handoff over existing command and candidate observations."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from polaris.cells.roles.runtime.public.contracts.engineering_run import ArtifactObservation
from polaris.kernelone.process import CommandOutputCapture


def requested_artifacts(value: object) -> tuple[str, ...]:
    """Validate model-selected relative names before allocating or executing."""
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("artifacts must be a list of at most 16 relative file names")
    names: list[str] = []
    for name in value:
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or "\0" in name
            or name.startswith("/")
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or name in names
        ):
            raise ValueError("artifacts must contain unique canonical relative file names")
        names.append(name)
    return tuple(names)


def inputs_digest(binding: Mapping[str, Any]) -> str:
    """Hash the existing review input manifest, without another candidate owner."""
    inputs = binding.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("artifact source input manifest is unavailable")
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def capture_artifacts(
    capture: CommandOutputCapture, names: tuple[str, ...], before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[ArtifactObservation, ...]:
    """Read only after the command's physical owner has proved drain."""
    original = inputs_digest(before)
    source = original if original == inputs_digest(after) else None
    return tuple(
        ArtifactObservation(
            artifact_ref="artifact_" + uuid4().hex,
            name=output.name,
            media_type=mimetypes.guess_type(output.name)[0] or "application/octet-stream",
            data_base64=base64.b64encode(output.data).decode("ascii"),
            sha256=hashlib.sha256(output.data).hexdigest(),
            source_digest=source,
        )
        for output in capture.read(names)
    )


def model_tool_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove artifact blobs only; preserve the existing image attribution shape."""
    result = dict(payload)
    if isinstance(result.get("artifacts"), (list, tuple)):
        result["artifacts"] = [
            {key: value for key, value in artifact.items() if key != "data_base64"} for artifact in result["artifacts"]
        ]
    return result
