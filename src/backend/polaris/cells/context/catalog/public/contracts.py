from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchCellsQueryV1:
    query: str
    limit: int = 10


@dataclass(frozen=True)
class CellDescriptorV1:
    cell_id: str
    title: str
    purpose: str
    domain: str
    kind: str
    visibility: str
    stateful: bool
    owner: str
    capability_summary: str


@dataclass(frozen=True)
class SearchCellsResultV1:
    descriptors: tuple[CellDescriptorV1, ...]
    total: int
