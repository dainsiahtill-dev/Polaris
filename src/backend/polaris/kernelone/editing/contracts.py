"""Value contracts for an editing rejection before a physical write begins."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


def hash_edit_prewrite_arguments(arguments: Mapping[str, object]) -> str:
    """Hash actual physical arguments, separately from native and DEO identities."""
    body = json.dumps(
        dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(b"kernelone.editing.prewrite_arguments.v1\0" + body).hexdigest()


@dataclass(frozen=True, slots=True)
class EditPrewriteRejectionV1:
    """Known validation failure with an observed unchanged regular-file preimage.

    The operation hash binds the caller's prepared context. ``arguments_hash``
    independently binds the actual physical input. Neither field grants authority.
    """

    reason_code: str
    target_path: str
    normalized_operation_hash: str
    arguments_hash: str
    before_sha256: str
    after_sha256: str
    write_started: Literal[False] = False
    schema_version: Literal["kernelone.editing.prewrite_rejection.v1"] = "kernelone.editing.prewrite_rejection.v1"

    def __post_init__(self) -> None:
        if self.reason_code not in {
            "edit_blocks_invalid_line_range",
            "edit_blocks_invalid_blocks",
            "edit_blocks_no_content_change",
        }:
            raise ValueError("unsupported prewrite rejection reason")
        if (
            not self.target_path
            or self.target_path.startswith(("/", "\\"))
            or any(part in {"", ".", ".."} for part in self.target_path.replace("\\", "/").split("/"))
        ):
            raise ValueError("prewrite target must be a relative file path")
        for value in (
            self.normalized_operation_hash,
            self.arguments_hash,
            self.before_sha256,
            self.after_sha256,
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("prewrite hashes must be canonical SHA256 values")
        if self.before_sha256 != self.after_sha256 or self.write_started is not False:
            raise ValueError("prewrite rejection requires an unchanged target before write")
        if self.schema_version != "kernelone.editing.prewrite_rejection.v1":
            raise ValueError("unsupported prewrite rejection schema")
