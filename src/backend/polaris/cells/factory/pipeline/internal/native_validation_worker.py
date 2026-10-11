"""Explicit private HOST bootstrap; guest synchronization carries no authority.

This executable retains the exact v1 protocol. Operations require a separate
v2 HOST contract and independently immutable deployment, never bootstrap ACK.
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO

from .workspace_binary_provenance import ProvenanceError, decode_json


class WorkerProtocolError(RuntimeError):
    """Malformed or unsupported HOST control; no Group is created."""


def read_control(source: BinaryIO) -> dict[str, Any]:
    line = source.readline(1025)
    if not line or len(line) > 1024 or not line.endswith(b"\n"):
        raise WorkerProtocolError("native_validation_worker_protocol_size")
    try:
        return decode_json(line, limit=1024)
    except ProvenanceError as exc:
        raise WorkerProtocolError("native_validation_worker_protocol_json") from exc


def _emit(output: BinaryIO, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
    if len(data) > 1024:
        raise WorkerProtocolError("native_validation_worker_protocol_size")
    if output.write(data) != len(data):
        raise WorkerProtocolError("native_validation_worker_protocol_write")
    output.flush()


def run_worker(source: BinaryIO, output: BinaryIO) -> int:
    _emit(output, {"version": 1, "event": "ready"})
    register = read_control(source)
    if (
        set(register) != {"version", "command", "bootstrapPortId", "challengeId", "nonce"}
        or type(register["version"]) is not int
        or register["version"] != 1
        or register["command"] != "register"
    ):
        raise WorkerProtocolError("native_validation_worker_protocol_register")
    import re

    if (
        any(
            type(register[name]) is not str or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", register[name]) is None
            for name in ("bootstrapPortId", "challengeId")
        )
        or type(register["nonce"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", register["nonce"]) is None
    ):
        raise WorkerProtocolError("native_validation_worker_protocol_context")
    _emit(output, {"version": 1, "event": "registered", "challengeId": register["challengeId"]})
    stop = read_control(source)
    if (
        set(stop) != {"version", "command"}
        or type(stop["version"]) is not int
        or stop["version"] != 1
        or stop["command"] != "stop"
    ):
        raise WorkerProtocolError("native_validation_worker_protocol_operation_unadmitted")
    return 0


def main() -> int:
    try:
        return run_worker(sys.stdin.buffer, sys.stdout.buffer)
    except (WorkerProtocolError, OSError) as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
