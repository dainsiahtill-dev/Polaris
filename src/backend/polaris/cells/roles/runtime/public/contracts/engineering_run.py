"""Small program interfaces for the engineering runtime, independent of legacy runs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Literal


@dataclass(frozen=True)
class RoleRunCommand:
    session_id: str
    role: Literal["pm", "chief_engineer", "director", "qa"]
    instruction: str
    workspace: Path
    workflow: Literal["direct", "collaborative"] = "direct"
    required_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserObservationConfig:
    """Host-selected read-only browser runtime, never a model-supplied grant."""

    executable: str
    python_executable: str
    python_paths: tuple[str, ...]
    require_visual_review: bool = False

    def __post_init__(self) -> None:
        for value in (self.executable, self.python_executable, *self.python_paths):
            if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
                raise ValueError("browser runtime paths must be absolute non-empty strings")
        if type(self.require_visual_review) is not bool:
            raise ValueError("visual review requirement must be a boolean")


@dataclass(frozen=True)
class PermissionScope:
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    enabled_tools: frozenset[str]
    network_allowed: bool
    browser: BrowserObservationConfig | None = None
    toolchain_bin_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RunLimits:
    deadline: float | None
    max_output_bytes: int
    max_tool_output_bytes: int


@dataclass(frozen=True)
class ImageObservation:
    """Bounded original image bytes produced by a trusted observation tool."""

    media_type: str
    data_base64: str
    width: int
    height: int
    sha256: str
    label: str

    def __post_init__(self) -> None:
        if self.media_type not in {"image/png", "image/jpeg"}:
            raise ValueError("unsupported observed image type")
        if any(type(value) is not int or not 1 <= value <= 4096 for value in (self.width, self.height)):
            raise ValueError("observed image dimensions are invalid")
        if not isinstance(self.label, str) or not self.label or len(self.label) > 256:
            raise ValueError("observed image label is invalid")
        if not isinstance(self.data_base64, str) or len(self.data_base64) > 1_400_000:
            raise ValueError("observed image bytes exceed the supported bound")
        try:
            raw = base64.b64decode(self.data_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("observed image is not valid base64") from error
        if not raw or len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("observed image bytes do not match their integrity evidence")


@dataclass(frozen=True)
class ArtifactObservation:
    """Bounded original command output, identified and bound by the host."""

    artifact_ref: str
    name: str
    media_type: str
    data_base64: str
    sha256: str
    source_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_ref, str) or re.fullmatch(r"artifact_[0-9a-f]{32}", self.artifact_ref) is None:
            raise ValueError("observed artifact reference is invalid")
        if (
            not isinstance(self.name, str)
            or not self.name
            or len(self.name) > 4096
            or "\\" in self.name
            or "\0" in self.name
            or self.name.startswith("/")
            or PureWindowsPath(self.name).drive
            or any(part in {"", ".", ".."} for part in self.name.split("/"))
        ):
            raise ValueError("observed artifact name must be a canonical relative file path")
        if self.media_type not in {"image/svg+xml", "text/html", "image/png", "image/jpeg"}:
            raise ValueError("unsupported observed artifact type")
        if self.source_digest is not None and (
            not isinstance(self.source_digest, str) or re.fullmatch(r"[0-9a-f]{64}", self.source_digest) is None
        ):
            raise ValueError("observed artifact source digest is invalid")
        if not isinstance(self.data_base64, str) or len(self.data_base64) > 1_333_336:
            raise ValueError("observed artifact bytes exceed the supported bound")
        try:
            raw = base64.b64decode(self.data_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("observed artifact is not valid base64") from error
        if len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("observed artifact bytes do not match their integrity evidence")


@dataclass(frozen=True)
class ToolObservation:
    call_id: str
    tool: str
    ok: bool
    content: str
    exit_code: int | None
    interrupted: bool
    # A physically drained command deadline is a failed observation, not a
    # cancelled run or an unobserved effect. Missing historical values stay false.
    timed_out: bool = False
    images: tuple[ImageObservation, ...] = ()
    artifacts: tuple[ArtifactObservation, ...] = ()


def tool_observation_payload(observation: ToolObservation) -> dict[str, Any]:
    """Preserve historical nonvisual payloads while persisting real attachments."""
    payload = asdict(observation)
    if not observation.images:
        payload.pop("images")
    if not observation.artifacts:
        payload.pop("artifacts")
    return payload


@dataclass(frozen=True)
class RoleRunResult:
    session_id: str
    role: str
    text: str
    status: Literal["completed", "failed", "cancelled", "interrupted"]
    verification: Literal["passed", "failed", "not_run", "unknown"]
    checks: tuple[ToolObservation, ...]
