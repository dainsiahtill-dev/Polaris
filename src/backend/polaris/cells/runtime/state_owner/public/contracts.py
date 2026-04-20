from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PersistRuntimeTaskStateCommandV1:
    task_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PersistRuntimeContractCommandV1:
    contract_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PersistRuntimeRunCommandV1:
    run_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class GetRuntimeSnapshotQueryV1:
    scope: str = "runtime"


@dataclass(frozen=True)
class GetRuntimeRunQueryV1:
    run_id: str


@dataclass(frozen=True)
class RuntimeStateWriteResultV1:
    path: str
    version: int
    updated: bool


@dataclass(frozen=True)
class RuntimeStateChangedEventV1:
    scope: str
    path: str


class RuntimeStateOwnerError(Exception):
    """Raised when runtime state ownership guarantees are violated."""


__all__ = [
    "GetRuntimeRunQueryV1",
    "GetRuntimeSnapshotQueryV1",
    "PersistRuntimeContractCommandV1",
    "PersistRuntimeRunCommandV1",
    "PersistRuntimeTaskStateCommandV1",
    "RuntimeStateChangedEventV1",
    "RuntimeStateOwnerError",
    "RuntimeStateWriteResultV1",
]
