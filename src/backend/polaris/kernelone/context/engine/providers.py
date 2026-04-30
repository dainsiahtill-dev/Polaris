"""Context providers implementation."""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Protocol

from polaris.kernelone.context.repo_map import build_repo_map
from polaris.kernelone.fs.text_ops import read_file_safe
from polaris.kernelone.memory.refs import has_memory_refs
from polaris.kernelone.storage import (
    resolve_ramdisk_root,
    resolve_workspace_persistent_path,
)
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .models import ContextItem, ContextRequest
from .utils import _estimate_tokens, _hash_text, _read_slice_spec, _read_tail_lines

logger = logging.getLogger(__name__)


class RetrievedMemory(Protocol):
    """Minimal memory shape required by the context engine."""

    id: str
    source_event_id: str
    text: str
    context: dict[str, object]


class MemoryStorePort(Protocol):
    """Read-only memory retrieval contract for context building."""

    def retrieve(
        self,
        query: str,
        current_step: int,
        top_k: int = 10,
        *,
        weights: dict[str, float] | None = None,
        return_scores: bool = False,
    ) -> Sequence[tuple[RetrievedMemory, float]] | Sequence[RetrievedMemory]:
        """Retrieve ranked memory items for a query."""


MemoryStoreFactory = Callable[[str], MemoryStorePort]


def _build_memory_store(memory_path: str) -> MemoryStorePort:
    from polaris.kernelone.memory.memory_store import MemoryStore

    return MemoryStore(memory_path)


class BaseProvider(ABC):
    name: str = "base"

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root

    @abstractmethod
    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        raise NotImplementedError

    def estimate_size(self, item: ContextItem) -> int:
        return _estimate_tokens(item.content_or_pointer)


class DocsProvider(BaseProvider):
    name = "docs"

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        paths = policy.get("docs_paths") or [
            "docs/agent/tui_runtime.md",
            "docs/product/requirements.md",
            "docs/product/product_spec.md",
            "docs/agent/architecture.md",
            "docs/agent/invariants.md",
        ]
        max_chars = int(policy.get("docs_max_chars", 2000) or 2000)
        items: list[ContextItem] = []
        for rel_path in paths:
            full_path = os.path.join(self.project_root, rel_path)
            if not os.path.exists(full_path):
                continue
            raw = read_file_safe(full_path)
            if not raw:
                continue
            content = raw[:max_chars] if max_chars > 0 else raw
            refs = {
                "path": rel_path,
                "file_hash": _hash_text(raw),
                "char_range": [0, len(content)],
            }
            item = ContextItem(
                kind="docs",
                content_or_pointer=content,
                refs=refs,
                size_est=_estimate_tokens(content),
                priority=int(policy.get("docs_priority", 7) or 7),
                reason=f"Core docs: {os.path.basename(rel_path)}",
                provider=self.name,
            )
            items.append(item)
        return items


class ContractProvider(BaseProvider):
    name = "contract"

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        paths = policy.get("contract_paths") or [
            "runtime/contracts/pm_tasks.contract.json",
            "runtime/contracts/plan.md",
        ]
        max_chars = int(policy.get("contract_max_chars", 4000) or 4000)
        cache_root = build_cache_root(resolve_ramdisk_root(None), self.project_root)
        items: list[ContextItem] = []
        for rel_path in paths:
            try:
                full_path = resolve_artifact_path(
                    self.project_root,
                    cache_root,
                    rel_path,
                    run_id=request.run_id,
                )
            except (RuntimeError, ValueError):
                full_path = os.path.join(self.project_root, rel_path)
            if not os.path.exists(full_path):
                continue
            raw = read_file_safe(full_path)
            if not raw:
                continue
            content = raw[:max_chars] if max_chars > 0 else raw
            refs = {
                "path": rel_path,
                "file_hash": _hash_text(raw),
                "char_range": [0, len(content)],
            }
            item = ContextItem(
                kind="contract",
                content_or_pointer=content,
                refs=refs,
                size_est=_estimate_tokens(content),
                priority=int(policy.get("contract_priority", 9) or 9),
                reason=f"Contract input: {os.path.basename(rel_path)}",
                provider=self.name,
            )
            items.append(item)
        return items


class MemoryProvider(BaseProvider):
    name = "memory"

    def __init__(
        self,
        project_root: str,
        *,
        memory_store_factory: MemoryStoreFactory | None = None,
    ) -> None:
        super().__init__(project_root)
        memory_path = resolve_workspace_persistent_path(project_root, "workspace/brain/MEMORY.jsonl")
        self.store = (memory_store_factory or _build_memory_store)(memory_path)

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        top_k = int(policy.get("memory_top_k", 5) or 5)
        max_chars = int(policy.get("memory_max_chars", 400) or 400)
        if not request.query or top_k <= 0:
            return []
        results = self.store.retrieve(request.query, request.step, top_k=top_k, return_scores=True)
        # When return_scores=True, results is Sequence[tuple[RetrievedMemory, float]]
        # Narrow the type by checking the first element
        scored_results: Sequence[tuple[RetrievedMemory, float]] = (
            results if results and isinstance(results[0], tuple) else ()
        )
        items: list[ContextItem] = []
        for mem, score in scored_results:
            has_refs = has_memory_refs(mem.context)
            if not has_refs and policy.get("memory_refs_required", False):
                continue
            text = mem.text or ""
            content = text[:max_chars] if max_chars > 0 else text
            refs = dict(mem.context or {})
            refs.update({"mem_id": mem.id, "source_event_id": mem.source_event_id})
            if not has_refs:
                refs["refs_missing"] = True
            reason = f"Retrieved memory (score={score:.3f})"
            kind = "memory" if has_refs else "note"
            priority = int(policy.get("memory_priority", 4) or 4)
            if not has_refs:
                priority = min(priority, 1)
                reason = reason + "; missing refs (downgraded)"
            items.append(
                ContextItem(
                    kind=kind,
                    content_or_pointer=content,
                    refs=refs,
                    size_est=_estimate_tokens(content),
                    priority=priority,
                    reason=reason,
                    provider=self.name,
                )
            )
        return items


class EventsProvider(BaseProvider):
    name = "events"

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        events_path = request.events_path or ""
        if not events_path or not os.path.exists(events_path):
            return []
        policy = request.policy or {}
        tail_lines = int(policy.get("events_tail_lines", 120) or 120)
        max_chars = int(policy.get("events_max_chars", 2000) or 2000)
        lines = _read_tail_lines(events_path, tail_lines)
        if not lines:
            return []
        content = "\n".join(lines)
        if max_chars > 0 and len(content) > max_chars:
            content = content[-max_chars:]
        refs = {"path": events_path, "tail_lines": tail_lines}
        item = ContextItem(
            kind="events",
            content_or_pointer=content,
            refs=refs,
            size_est=_estimate_tokens(content),
            priority=int(policy.get("events_priority", 6) or 6),
            reason="Recent events tail",
            provider=self.name,
        )
        return [item]


class RepoEvidenceProvider(BaseProvider):
    name = "repo_evidence"

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        evidence_specs = policy.get("repo_evidence") or []
        max_chars = int(policy.get("repo_evidence_max_chars", 1200) or 1200)
        items: list[ContextItem] = []
        if not isinstance(evidence_specs, list):
            logger.warning(
                "RepoEvidenceProvider: evidence_specs is not a list (type=%s), returning empty",
                type(evidence_specs).__name__,
            )
            return items
        for spec in evidence_specs:
            if not isinstance(spec, dict):
                continue
            rel_path = str(spec.get("path") or "").strip()
            if not rel_path:
                logger.warning("RepoEvidenceProvider: spec missing 'path', skipping: %s", spec)
                continue
            full_path = os.path.join(self.project_root, rel_path)
            if not os.path.exists(full_path):
                logger.warning("RepoEvidenceProvider: file not found, skipping: %s", rel_path)
                continue
            content, line_range, file_hash = _read_slice_spec(full_path, spec)
            if not content:
                logger.warning(
                    "RepoEvidenceProvider: empty content after parse, skipping: %s",
                    rel_path,
                )
                continue
            if max_chars > 0 and len(content) > max_chars:
                content = content[:max_chars] + "...[truncated]"
            refs = {
                "path": rel_path,
                "line_range": line_range,
                "file_hash": file_hash,
            }
            reason = str(spec.get("reason") or "Repo evidence slice").strip()
            items.append(
                ContextItem(
                    kind="evidence",
                    content_or_pointer=content,
                    refs=refs,
                    size_est=_estimate_tokens(content),
                    priority=int(spec.get("priority", 8) or 8),
                    reason=reason,
                    provider=self.name,
                )
            )
        return items


class RepoMapProvider(BaseProvider):
    name = "repo_map"

    def collect_items(self, request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        languages = policy.get("repo_map_languages")
        if isinstance(languages, str):
            languages = [part.strip() for part in languages.split(",") if part.strip()]
        max_files = int(policy.get("repo_map_max_files", 200) or 200)
        max_lines = int(policy.get("repo_map_max_lines", 200) or 200)
        per_file_lines = int(policy.get("repo_map_per_file_lines", 12) or 12)
        include_glob = policy.get("repo_map_include")
        exclude_glob = policy.get("repo_map_exclude")
        max_chars = int(policy.get("repo_map_max_chars", 0) or 0)
        repo_map = build_repo_map(
            self.project_root,
            languages=languages if isinstance(languages, list) else None,
            max_files=max_files,
            max_lines=max_lines,
            per_file_lines=per_file_lines,
            include_glob=include_glob if isinstance(include_glob, str) else None,
            exclude_glob=exclude_glob if isinstance(exclude_glob, str) else None,
        )
        text = repo_map.get("text") or ""
        if not text:
            logger.warning(
                "RepoMapProvider: repo_map returned empty text for project: %s",
                self.project_root,
            )
            return []
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "...[truncated]"
        refs = dict(repo_map.get("stats") or {})
        refs.update(
            {
                "path": "<repo_map>",
                "languages": repo_map.get("languages"),
                "truncated": repo_map.get("truncated", False),
            }
        )
        item = ContextItem(
            kind="repo_map",
            content_or_pointer=text,
            refs=refs,
            size_est=_estimate_tokens(text),
            priority=int(policy.get("repo_map_priority", 8) or 8),
            reason="Repository skeleton map",
            provider=self.name,
        )
        return [item]
