"""Context engine implementation."""

import asyncio
import concurrent.futures
import json
import logging
import os
from typing import Any

from polaris.kernelone.events.io_events import emit_event
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage import resolve_ramdisk_root
from polaris.kernelone.storage.io_paths import (
    build_cache_root,
    resolve_run_dir,
)
from polaris.kernelone.utils.time_utils import _utc_now

from .cache import ContextCache
from .models import ContextBudget, ContextItem, ContextPack, ContextRequest
from .providers import (
    BaseProvider,
    ContractProvider,
    DocsProvider,
    EventsProvider,
    MemoryProvider,
    RepoEvidenceProvider,
    RepoMapProvider,
)
from .utils import _estimate_tokens, _hash_text, _safe_json

logger = logging.getLogger(__name__)


class ContextEngine:
    def __init__(self, project_root: str, *, cache: ContextCache | None = None) -> None:
        self.project_root = project_root
        self.cache = cache or ContextCache()
        self.providers: dict[str, BaseProvider] = {
            DocsProvider.name: DocsProvider(project_root),
            ContractProvider.name: ContractProvider(project_root),
            MemoryProvider.name: MemoryProvider(project_root),
            EventsProvider.name: EventsProvider(project_root),
            RepoEvidenceProvider.name: RepoEvidenceProvider(project_root),
            RepoMapProvider.name: RepoMapProvider(project_root),
        }

    def build_context(self, request: ContextRequest) -> ContextPack:
        request_hash = self._hash_request(request)
        cached = self.cache.get_cached_pack(request_hash)
        if cached:
            return cached

        enabled = set(request.sources_enabled or self.providers.keys())
        items: list[ContextItem] = []
        for name, provider in self.providers.items():
            if name not in enabled:
                continue
            items.extend(provider.collect_items(request))

        items = self._apply_role_strategy(items, request)
        items = self._fill_item_sizes(items)
        budget = request.budget
        compression_log: list[dict[str, Any]] = []
        policy = request.policy if isinstance(request.policy, dict) else {}
        token_budget_override = int(policy.get("token_budget", 0) or 0)
        char_budget_override = int(policy.get("max_context_chars", 0) or 0)
        if token_budget_override > 0 or char_budget_override > 0:
            budget = request.budget.model_copy(
                update={
                    "max_tokens": token_budget_override
                    if token_budget_override > 0
                    else int(request.budget.max_tokens or 0),
                    "max_chars": char_budget_override
                    if char_budget_override > 0
                    else int(request.budget.max_chars or 0),
                }
            )
            compression_log.append(
                {
                    "action": "policy_budget_override",
                    "max_tokens": int(budget.max_tokens or 0),
                    "max_chars": int(budget.max_chars or 0),
                }
            )
        items, ladder_log = self._apply_budget_ladder(items, budget, request)
        compression_log.extend(ladder_log)

        rendered_prompt = self._render_prompt(items, request)
        rendered_messages = [{"role": "user", "content": rendered_prompt}]
        total_chars = len(rendered_prompt)
        total_tokens = _estimate_tokens(rendered_prompt)

        pack = ContextPack(
            request_hash=request_hash,
            items=items,
            compression_log=compression_log,
            rendered_prompt=rendered_prompt,
            rendered_messages=rendered_messages,
            total_tokens=total_tokens,
            total_chars=total_chars,
            build_timestamp=_utc_now(),
        )

        snapshot_path, snapshot_hash = self._maybe_snapshot(pack, request)
        if snapshot_path:
            pack.snapshot_path = snapshot_path
        if snapshot_hash:
            pack.snapshot_hash = snapshot_hash

        self.cache.cache_pack(pack)
        self._emit_context_events(pack, request)
        return pack

    def _hash_request(self, request: ContextRequest) -> str:
        payload = request.model_dump()
        payload["budget"] = request.budget.model_dump()
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return _hash_text(raw)

    def _apply_role_strategy(self, items: list[ContextItem], request: ContextRequest) -> list[ContextItem]:
        policy = request.policy or {}
        max_items = int(policy.get("max_items", 0) or 0)
        forbidden = set(policy.get("forbidden_providers", []) or [])
        required = set(policy.get("required_providers", []) or [])
        memory_limit = int(policy.get("memory_limit", 0) or 0)

        filtered = [item for item in items if item.provider not in forbidden]
        if required:
            for provider in required:
                if not any(item.provider == provider for item in filtered):
                    pass

        if memory_limit > 0:
            memory_items = [i for i in filtered if i.provider == "memory"]
            non_memory = [i for i in filtered if i.provider != "memory"]
            memory_items = memory_items[:memory_limit]
            filtered = non_memory + memory_items

        if max_items > 0 and len(filtered) > max_items:
            filtered.sort(key=lambda i: i.priority, reverse=True)
            filtered = filtered[:max_items]
        return filtered

    def _fill_item_sizes(self, items: list[ContextItem]) -> list[ContextItem]:
        for item in items:
            if not item.size_est:
                item.size_est = _estimate_tokens(item.content_or_pointer)
        return items

    def _apply_budget_ladder(
        self, items: list[ContextItem], budget: ContextBudget, request: ContextRequest | None = None
    ) -> tuple[list[ContextItem], list[dict[str, Any]]]:
        compression_log: list[dict[str, Any]] = []
        compact_now = False
        compact_focus = ""
        task_identity: dict[str, Any] = {}
        if request:
            compact_now = request.compact_now
            compact_focus = request.compact_focus
            task_identity = request.task_identity or {}

        deduped = self._deduplicate(items)
        if len(deduped) < len(items):
            compression_log.append({"action": "deduplicate", "removed": len(items) - len(deduped)})
        items = deduped

        if self._over_budget(items, budget):
            items = self._trim_items(items, budget.max_chars)
            compression_log.append({"action": "trim_items"})

        if self._over_budget(items, budget):
            items = self._pointerize_items(items)
            compression_log.append({"action": "pointerize"})

        if self._over_budget(items, budget) or compact_now:
            if compact_now:
                items, llm_summary = self._summarize_items_llm(items, task_identity, compact_focus)
                compression_log.append(
                    {
                        "action": "summarize",
                        "method": "llm",
                        "llm_summary_used": True,
                        "compact_focus": compact_focus,
                        "summary_text": llm_summary[:500],
                    }
                )
            else:
                items = self._summarize_items(items)
                compression_log.append({"action": "summarize", "method": "heuristic"})

        if self._over_budget(items, budget):
            items = self._drop_low_priority(items, budget)
            compression_log.append({"action": "drop_low_priority", "remaining": len(items)})

        if task_identity and (compact_now or any("summarize" in log.get("action", "") for log in compression_log)):
            identity_item = ContextItem(
                kind="identity",
                provider="engine",
                content_or_pointer=f"[Task Identity Re-injected]\n- task_id: {task_identity.get('task_id', 'unknown')}\n- goal: {task_identity.get('goal', 'unknown')}\n- acceptance: {task_identity.get('acceptance', [])}\n- write_scope: {task_identity.get('write_scope', [])}",
                size_est=_estimate_tokens(str(task_identity)),
                priority=200,
                refs={},
                reason="Identity re-injection after compression",
            )
            items.insert(0, identity_item)
            compression_log.append({"action": "identity_reinject"})

        return items, compression_log

    def _over_budget(self, items: list[ContextItem], budget: ContextBudget) -> bool:
        if not budget:
            return False
        token_limit = int(budget.max_tokens or 0)
        char_limit = int(budget.max_chars or 0)
        tokens = sum(item.size_est for item in items)
        chars = sum(len(item.content_or_pointer or "") for item in items)
        if token_limit > 0 and tokens > token_limit:
            return True
        return bool(char_limit > 0 and chars > char_limit)

    def _deduplicate(self, items: list[ContextItem]) -> list[ContextItem]:
        seen: dict[str, ContextItem] = {}
        for item in items:
            key = self._source_key(item)
            if not key:
                key = item.id
            existing = seen.get(key)
            if not existing or item.priority > existing.priority:
                seen[key] = item
        return list(seen.values())

    def _source_key(self, item: ContextItem) -> str:
        refs = item.refs or {}
        for key in ("path", "file_path", "artifact_path"):
            value = refs.get(key)
            if value:
                return str(value)
        return ""

    def _trim_items(self, items: list[ContextItem], max_chars: int = 600) -> list[ContextItem]:
        """Trim items proportionally based on actual overage from budget.

        Unlike the previous hardcoded 600-char threshold, this method:
        1. Respects the actual budget constraint (max_chars)
        2. Trims proportionally based on how much we're over budget
        3. Prioritizes trimming lower-priority items more aggressively
        4. Only trims items with content > 200 chars (small items preserved)

        Args:
            items: Context items to trim
            max_chars: Budget max_chars limit (0 means no limit)

        Returns:
            List of trimmed items
        """
        if max_chars <= 0:
            return items

        total_chars = sum(len(item.content_or_pointer or "") for item in items)
        if total_chars <= max_chars:
            return items

        over_budget = total_chars - max_chars
        overage_ratio = over_budget / total_chars

        sorted_items = sorted(items, key=lambda x: x.priority)
        trimmed: list[ContextItem] = []

        for item in sorted_items:
            content = item.content_or_pointer or ""
            item_len = len(content)

            if item_len > 200:
                priority_factor = max(0.3, 1.0 - (item.priority / 200.0) * 0.5)
                effective_trim_ratio = min(overage_ratio * priority_factor, 0.9)
                target_len = max(100, int(item_len * (1 - effective_trim_ratio)))

                item.content_or_pointer = content[:target_len] + "...[trimmed]"
                item.size_est = _estimate_tokens(item.content_or_pointer)

            trimmed.append(item)

        return trimmed

    def _pointerize_items(self, items: list[ContextItem]) -> list[ContextItem]:
        pointerized: list[ContextItem] = []
        for item in items:
            refs = item.refs or {}
            path = refs.get("path") or refs.get("file_path") or refs.get("artifact_path")
            if path:
                pointer = f"[See {path}]"
                item.content_or_pointer = pointer
                item.size_est = _estimate_tokens(pointer)
                item.kind = "pointer"
            pointerized.append(item)
        return pointerized

    def _summarize_items(
        self, items: list[ContextItem], head_chars: int = 200, tail_chars: int = 200
    ) -> list[ContextItem]:
        summarized: list[ContextItem] = []
        for item in items:
            content = item.content_or_pointer or ""
            if len(content) > head_chars + tail_chars + 16:
                summary = content[:head_chars] + "...[snip]..." + content[-tail_chars:]
                item.content_or_pointer = summary
                item.size_est = _estimate_tokens(summary)
            summarized.append(item)
        return summarized

    def _summarize_items_llm(
        self,
        items: list[ContextItem],
        task_identity: dict[str, Any] | None = None,
        compact_focus: str = "",
    ) -> tuple[list[ContextItem], str]:
        """Summarize context for compression using LLM.

        Attempts to generate a concise summary via LLM, falling back to
        deterministic summarization on any error.
        """
        if not items:
            return items, ""

        # Build combined content from items
        combined_content = "\n\n---\n\n".join(
            f"[{item.kind}] {item.content_or_pointer}" for item in items if item.content_or_pointer
        )

        # Build task identity section
        identity_section = ""
        if task_identity:
            identity_section = f"""
Task Identity:
- task_id: {task_identity.get("task_id", "unknown")}
- goal: {task_identity.get("goal", "unknown")}
- acceptance: {task_identity.get("acceptance", [])}
- write_scope: {task_identity.get("write_scope", [])}
"""

        # Build focus section if provided
        focus_section = ""
        if compact_focus:
            focus_section = f"\nFocus for summarization: {compact_focus}\n"

        # Construct the summarization prompt
        prompt = f"""You are a context compression assistant. Given the following context items, generate a concise summary that preserves the most important information.

{combined_content}
{identity_section}
{focus_section}
Generate a concise summary (max 500 tokens) that preserves:
1. Key decisions and their rationale
2. Important technical details (file paths, function names, configurations)
3. Current task progress and next steps
4. Any unresolved issues or questions

Summary:"""

        try:

            from polaris.kernelone.llm.engine.executor import AIRequest, TaskType, get_executor_async

            async def _call_llm() -> str:
                executor = await get_executor_async(self.project_root)
                request = AIRequest(
                    task_type=TaskType.GENERATION,
                    role="system",
                    input=prompt,
                    options={
                        "temperature": 0.3,
                        "max_tokens": 600,
                    },
                )
                response = await executor.invoke(request)
                if not response.ok:
                    raise RuntimeError(f"LLM summarization failed: {response.error}")
                return response.output.strip()

            # Use ThreadPoolExecutor to avoid nested event loop issues
            # This works whether called from sync or async context
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                summary_text = pool.submit(lambda: asyncio.run(_call_llm())).result()

            logger.info("LLM summarization succeeded: %s", summary_text[:100])

        except (RuntimeError, ValueError) as exc:
            logger.warning("LLM summarization failed (%s); using deterministic fallback", exc)
            # Deterministic fallback: preserve structure but use heuristics
            summary_sections: list[str] = ["Context continuity summary"]
            if identity_section.strip():
                summary_sections.append(identity_section.strip())

            summary_sections.append("Key context items:")
            for item in items[:8]:
                snippet = str(item.content_or_pointer or "").strip().replace("\r", " ").replace("\n", " ")
                if len(snippet) > 240:
                    snippet = snippet[:240].rstrip() + "..."
                summary_sections.append(f"- [{item.kind}/{item.provider}] {snippet or '[empty]'}")
            summary_text = "\n".join(summary_sections).strip()

        summary_item = ContextItem(
            kind="summary",
            provider="engine",
            content_or_pointer=summary_text,
            size_est=_estimate_tokens(summary_text),
            priority=100,
            refs={},
            reason="LLM summary for context continuity",
        )

        return [summary_item], summary_text

    def _drop_low_priority(self, items: list[ContextItem], budget: ContextBudget) -> list[ContextItem]:
        if not items:
            return items
        sorted_items = sorted(items, key=lambda i: i.priority, reverse=True)
        kept: list[ContextItem] = []
        for item in sorted_items:
            kept.append(item)
            if not self._over_budget(kept, budget):
                continue
        while self._over_budget(kept, budget) and kept:
            kept.pop()
        return kept

    def _render_prompt(self, items: list[ContextItem], request: ContextRequest) -> str:
        lines = [
            "# Context Pack",
            f"- run_id: {request.run_id}",
            f"- step: {request.step}",
            f"- role: {request.role}",
            f"- mode: {request.mode}",
        ]
        for item in items:
            lines.append("")
            lines.append(f"## {item.kind.upper()} ({item.provider})")
            if item.reason:
                lines.append(f"Reason: {item.reason}")
            if item.refs:
                lines.append(f"Refs: {_safe_json(item.refs)}")
            lines.append(item.content_or_pointer or "")
        return "\n".join(lines).strip() + "\n"

    def _emit_context_events(self, pack: ContextPack, request: ContextRequest) -> None:
        if not request.events_path:
            return
        refs = {
            "run_id": request.run_id,
            "step": request.step,
            "phase": request.mode,
            "task_id": request.task_id,
        }
        emit_event(
            request.events_path,
            kind="observation",
            actor="System",
            name="context.build",
            refs=refs,
            summary=f"ContextPack built ({len(pack.items)} items)",
            output={
                "request_hash": pack.request_hash,
                "items_count": len(pack.items),
                "providers_used": sorted({i.provider for i in pack.items}),
                "total_tokens": pack.total_tokens,
                "total_chars": pack.total_chars,
                "snapshot_path": pack.snapshot_path,
                "snapshot_hash": pack.snapshot_hash,
                "compression_log": pack.compression_log,
            },
        )
        if pack.snapshot_path:
            emit_event(
                request.events_path,
                kind="observation",
                actor="System",
                name="context.snapshot",
                refs=refs,
                summary="Context snapshot stored",
                output={
                    "request_hash": pack.request_hash,
                    "snapshot_path": pack.snapshot_path,
                    "snapshot_hash": pack.snapshot_hash,
                },
            )
        for item in pack.items:
            emit_event(
                request.events_path,
                kind="observation",
                actor="System",
                name="context.item",
                refs=refs,
                summary=f"{item.kind}:{item.id}",
                output={
                    "item_id": item.id,
                    "kind": item.kind,
                    "provider": item.provider,
                    "size_est": item.size_est,
                    "priority": item.priority,
                    "reason": item.reason,
                    "refs": item.refs,
                },
            )

    def _maybe_snapshot(self, pack: ContextPack, request: ContextRequest) -> tuple[str, str]:
        policy = request.policy or {}
        enabled = policy.get("snapshot_context")
        if enabled is None:
            enabled = str(os.environ.get("KERNELONE_CONTEXT_SNAPSHOT", "1")).strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if not enabled:
            return "", ""
        if not request.run_id:
            return "", ""
        cache_root = build_cache_root(resolve_ramdisk_root(None), self.project_root) or ""
        run_dir = resolve_run_dir(self.project_root, cache_root, request.run_id)
        evidence_dir = os.path.join(run_dir, "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        snapshot_name = f"context_snapshot_{pack.request_hash[:8]}.json"
        snapshot_path = os.path.join(evidence_dir, snapshot_name)
        snapshot_payload = {
            "request": request.model_dump(),
            "pack": pack.model_dump(),
            "snapshot_path": snapshot_path,
        }
        text = json.dumps(snapshot_payload, ensure_ascii=False, indent=2, default=str)
        write_text_atomic(snapshot_path, text + "\n")
        return snapshot_path, _hash_text(text)
