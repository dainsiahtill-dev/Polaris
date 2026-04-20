from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any

from polaris.kernelone import _runtime_config
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.embedding import get_default_embedding_port
from pydantic import ValidationError

from .refs import has_memory_refs
from .schema import MemoryItem

logger = logging.getLogger(__name__)

# Use _runtime_config for KERNELONE_* / POLARIS_* fallback
EMBEDDING_MODEL = _runtime_config.resolve_env_str("embedding_model") or "nomic-embed-text"
_MEMORY_REFS_MODE_ENV = "KERNELONE_MEMORY_REFS_MODE"

QUERY_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "default": {"rel": 0.5, "rec": 0.3, "imp": 0.2},
    "pm": {"rel": 0.52, "rec": 0.28, "imp": 0.2},
    "error": {"rel": 0.65, "rec": 0.2, "imp": 0.15},
    "architecture": {"rel": 0.58, "rec": 0.22, "imp": 0.2},
    "execution": {"rel": 0.56, "rec": 0.24, "imp": 0.2},
    "history": {"rel": 0.42, "rec": 0.38, "imp": 0.2},
    "time": {"rel": 0.3, "rec": 0.5, "imp": 0.2},
}

SYNONYM_DICT: dict[str, set[str]] = {
    "任务": {"task", "job", "todo", "待办"},
    "task": {"任务", "job", "todo", "priority"},
    "错误": {"error", "bug", "exception", "失败"},
    "error": {"错误", "bug", "failure", "异常"},
    "测试": {"test", "testing", "pytest", "单测"},
    "test": {"测试", "testing", "pytest", "unit"},
    "架构": {"architecture", "design", "system"},
    "architecture": {"架构", "设计", "system"},
}

_QUERY_PATTERNS: dict[str, list[str]] = {
    "error": [
        r"错误",
        r"失败",
        r"异常",
        r"报错",
        r"\berror\b",
        r"\bfail(?:ed|ure)?\b",
        r"\bexception\b",
        r"\b500\b",
        r"\bbug\b",
    ],
    "pm": [
        r"任务",
        r"待办",
        r"优先级",
        r"进度",
        r"\btask\b",
        r"\bpriority\b",
        r"\btodo\b",
        r"\bbacklog\b",
    ],
    "architecture": [
        r"架构",
        r"设计",
        r"接口",
        r"微服务",
        r"模块",
        r"\barchitecture\b",
        r"\bdesign\b",
        r"\bapi\b",
        r"\bschema\b",
    ],
    "execution": [
        r"运行",
        r"启动",
        r"部署",
        r"执行",
        r"\brun\b",
        r"\bstart\b",
        r"\bdeploy\b",
        r"\bexecute\b",
        r"\bbuild\b",
    ],
    "history": [
        r"之前",
        r"上次",
        r"历史",
        r"曾经",
        r"\bprevious\b",
        r"\blast\b",
        r"\bhistory\b",
        r"\bbefore\b",
    ],
    "time": [
        r"最近",
        r"最新",
        r"刚刚",
        r"上一步",
        r"\brecent\b",
        r"\blatest\b",
        r"\bnow\b",
        r"\btime\b",
    ],
}

_QUERY_TYPE_ORDER: tuple[str, ...] = (
    "error",
    "pm",
    "architecture",
    "execution",
    "history",
    "time",
)


def _memory_refs_mode() -> str:
    raw = os.environ.get(_MEMORY_REFS_MODE_ENV, "soft").strip().lower()
    if raw in ("off", "disabled", "none", "0", "false", "no"):
        return "off"
    if raw in ("strict", "soft"):
        return raw
    return "soft"


def _has_refs(context: dict[str, Any] | None) -> bool:
    """Backward-compatible alias (deprecated)."""
    return has_memory_refs(context)


def _detect_query_type(query: str) -> str:
    text = str(query or "").strip().lower()
    if not text:
        return "default"
    for query_type in _QUERY_TYPE_ORDER:
        patterns = _QUERY_PATTERNS.get(query_type, [])
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return query_type
    return "default"


def _expand_with_synonyms(terms: set[str]) -> set[str]:
    expanded: set[str] = set(terms)
    for term in list(terms):
        lookup = str(term).strip().lower()
        if not lookup:
            continue
        expanded |= SYNONYM_DICT.get(lookup, set())
    return expanded


class BM25:
    """Lightweight BM25 scorer used by memory retrieval."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.N = 0
        self.avgdl = 0.0
        self.doc_freqs: dict[str, int] = {}
        self.doc_lens: list[int] = []
        self.term_freqs: list[dict[str, int]] = []

    def _tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return [
            token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", str(text).lower()) if token.strip()
        ]

    def fit(self, documents: list[str]) -> None:
        self.N = len(documents)
        self.doc_freqs = {}
        self.doc_lens = []
        self.term_freqs = []

        for doc in documents:
            tokens = self._tokenize(doc)
            self.doc_lens.append(len(tokens))
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            self.term_freqs.append(tf)
            for token in set(tokens):
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        self.avgdl = (sum(self.doc_lens) / self.N) if self.N > 0 else 0.0

    def score(self, query: str) -> list[float]:
        if self.N == 0:
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return [0.0 for _ in range(self.N)]

        scores = [0.0 for _ in range(self.N)]
        for term in query_terms:
            df = self.doc_freqs.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + ((self.N - df + 0.5) / (df + 0.5)))
            for idx, tf in enumerate(self.term_freqs):
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue
                dl = self.doc_lens[idx] or 1
                denom = freq + self.k1 * (1 - self.b + self.b * (dl / max(self.avgdl, 1.0)))
                scores[idx] += idf * (freq * (self.k1 + 1.0)) / max(denom, 1e-9)
        return scores


class QueryCache:
    """TTL + LRU cache for retrieval results."""

    def __init__(self, max_size: int = 256, default_ttl: float = 10.0) -> None:
        self.max_size = max(1, int(max_size))
        self.default_ttl = max(0.1, float(default_ttl))
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, list[tuple[MemoryItem, float]]]] = OrderedDict()
        self._lock = threading.RLock()

    def _make_key(
        self,
        query: str,
        current_step: int,
        top_k: int,
        weights: dict[str, float],
    ) -> tuple[Any, ...]:
        normalized_weights = tuple(sorted((str(k), round(float(v), 6)) for k, v in dict(weights or {}).items()))
        return (str(query or ""), int(current_step), int(top_k), normalized_weights)

    def get(
        self,
        query: str,
        current_step: int,
        top_k: int,
        weights: dict[str, float],
    ) -> list[tuple[MemoryItem, float]] | None:
        key = self._make_key(query, current_step, top_k, weights)
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expire_at, payload = entry
            if expire_at < now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return payload

    def set(
        self,
        query: str,
        current_step: int,
        top_k: int,
        weights: dict[str, float],
        payload: list[tuple[MemoryItem, float]],
        ttl: float | None = None,
    ) -> None:
        key = self._make_key(query, current_step, top_k, weights)
        expire_at = time.time() + (float(ttl) if ttl is not None else self.default_ttl)
        with self._lock:
            self._cache[key] = (expire_at, payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class MemoryStore:
    def __init__(
        self,
        memory_file: str,
        *,
        enable_cache: bool = True,
        cache_max_size: int = 256,
        cache_ttl: float = 10.0,
        kernel_db: KernelDatabase | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.memory_file = memory_file
        self.memories: list[MemoryItem] = []
        self.db: Any = None
        self._kernel_db = kernel_db or KernelDatabase(
            os.path.dirname(os.path.abspath(self.memory_file)) or ".",
            allow_unmanaged_absolute=True,
        )
        self._embedding_model = str(embedding_model or EMBEDDING_MODEL).strip() or EMBEDDING_MODEL
        self._bm25 = BM25()
        self._cache: QueryCache | None = (
            QueryCache(max_size=cache_max_size, default_ttl=cache_ttl) if enable_cache else None
        )
        self._store_lock = threading.RLock()
        self._load()

    @property
    def _memory_lock_path(self) -> str:
        return f"{self.memory_file}.lock"

    def _read_memories_from_disk(self) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        if not os.path.exists(self.memory_file):
            return items
        with open(self.memory_file, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data.get("timestamp"), str):
                        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                    items.append(MemoryItem(**data))
                except (json.JSONDecodeError, ValidationError) as exc:
                    logger.warning("Failed to parse memory item from JSONL: %s (%s)", type(exc).__name__, exc)
                    continue
        return items

    def _load(self) -> None:
        """Loads memories from JSONL file."""
        self.memories = self._read_memories_from_disk()
        db_path = os.path.join(os.path.dirname(self.memory_file), "lancedb")
        try:
            self.db = self._kernel_db.lancedb(db_path, ensure_exists=True)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to initialize LanceDB memory store: %s", exc)
            self.db = None

        self._rebuild_bm25_index()

    def _get_embedding(self, text: str) -> list[float]:
        token = str(text or "").strip()
        if not token:
            return []
        try:
            return get_default_embedding_port().get_embedding(token, model=self._embedding_model)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Embedding unavailable for memory retrieval: %s", exc)
            return []

    def _rebuild_bm25_index(self) -> None:
        docs = []
        for mem in self.memories:
            keywords = " ".join(str(k) for k in (mem.keywords or []))
            docs.append(f"{mem.text or ''} {keywords}".strip())
        self._bm25.fit(docs)
        if self._cache:
            self._cache.clear()

    def append(self, item: MemoryItem) -> None:
        """Appends a memory item to the store and file."""
        mode = _memory_refs_mode()
        if mode != "off":
            has_refs = has_memory_refs(item.context)
            if mode == "strict" and not has_refs:
                logger.warning("Skipping memory item without evidence refs (strict mode).")
                return
            if mode == "soft" and not has_refs:
                context = dict(item.context or {})
                context.setdefault("refs_missing", True)
                context.setdefault("refs_mode", "soft")
                item.context = context
        with self._store_lock:
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with file_lock(self._memory_lock_path, timeout_sec=5.0) as acquired:
                if not acquired:
                    raise TimeoutError(f"Timed out locking memory store: {self.memory_file}")
                current_items = self._read_memories_from_disk()
                with open(self.memory_file, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(item.model_dump_json() + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                current_items.append(item)
                self.memories = current_items

        # Add to LanceDB
        if self.db is not None and item.text:
            vec = self._get_embedding(item.text)
            if vec:
                # Prepare record
                record = item.model_dump()
                record["vector"] = vec
                # timestamp to str if datetime
                if isinstance(record.get("timestamp"), datetime):
                    record["timestamp"] = record["timestamp"].isoformat()

                try:
                    table = self.db.open_table("memories")
                    table.add([record])
                except (RuntimeError, ValueError) as exc:
                    # Table might not exist, try creating it
                    logger.warning("kernelone.memory.memory_store.open_table failed: %s", exc, exc_info=True)
                    try:
                        self.db.create_table("memories", data=[record])
                    except (RuntimeError, ValueError) as e:
                        logger.debug("Failed to write memory vector to LanceDB: %s", e)

        self._rebuild_bm25_index()

    def _is_valid_memory_id(self, memory_id: str) -> bool:
        """Validate that memory_id is a valid memory identifier format."""
        try:
            raw = str(memory_id)
            # Support both plain UUIDs and prefixed IDs like mem_<uuid>
            if raw.startswith("mem_") or raw.startswith("ref_"):
                raw = raw[4:]
            uuid.UUID(raw)
            return True
        except (ValueError, AttributeError):
            return False

    def delete(self, memory_id: str) -> bool:
        """Deletes a memory item by ID."""
        if not self._is_valid_memory_id(memory_id):
            logger.warning("Invalid memory_id format rejected: %.20s", memory_id)
            return False

        with self._store_lock, file_lock(self._memory_lock_path, timeout_sec=5.0) as acquired:
            if not acquired:
                raise TimeoutError(f"Timed out locking memory store: {self.memory_file}")
            current_items = self._read_memories_from_disk()
            filtered_items = [memory for memory in current_items if memory.id != memory_id]
            if len(filtered_items) == len(current_items):
                return False
            payload = "".join(item.model_dump_json() + "\n" for item in filtered_items)
            write_text_atomic(self.memory_file, payload, lock_timeout_sec=None)
            self.memories = filtered_items

        # Delete from LanceDB using parameterized filter
        if self.db is not None:
            try:
                table = self.db.open_table("memories")
                table.delete(f"id = '{memory_id}'")
            except (RuntimeError, ValueError) as e:
                logger.debug("Failed to delete memory from LanceDB: %s", e)

        self._rebuild_bm25_index()
        return True

    def _adaptive_decay_tau(self, query_type: str, current_step: int) -> float:
        base = max(8.0, min(30.0, (float(current_step or 0) / 8.0) if current_step > 0 else 10.0))
        if query_type == "error":
            return max(4.0, base * 0.6)
        if query_type in ("history", "time"):
            return base * 1.6
        if query_type == "pm":
            return base * 1.2
        return base

    def _get_dynamic_weights(
        self,
        query: str,
        query_type: str,
        user_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        if user_weights is not None:
            return dict(user_weights)
        detected = query_type if query_type in QUERY_TYPE_WEIGHTS else _detect_query_type(query)
        return dict(QUERY_TYPE_WEIGHTS.get(detected, QUERY_TYPE_WEIGHTS["default"]))

    def retrieve(
        self,
        query: str,
        current_step: int,
        top_k: int = 10,
        weights: dict[str, float] | None = None,
        return_scores: bool = False,
    ) -> list[tuple[MemoryItem, float]] | list[MemoryItem]:
        """
        Retrieves relevant memories.
        If return_scores is True, returns List[Tuple[MemoryItem, float]]
        Else returns List[MemoryItem]
        """
        if not self.memories:
            return []

        query_type = _detect_query_type(query)
        resolved_weights = self._get_dynamic_weights(query, query_type, weights)

        if self._cache:
            cached = self._cache.get(query, current_step, top_k, resolved_weights)
            if cached is not None:
                if return_scores:
                    return cached
                return [item for item, _score in cached]

        scored_memories: list[tuple[float, MemoryItem]] = []
        query_terms = set(self._bm25._tokenize(query))
        query_terms = _expand_with_synonyms(query_terms)

        decay_tau = self._adaptive_decay_tau(query_type, current_step)

        bm25_scores = self._bm25.score(query)
        max_bm25 = max(bm25_scores) if bm25_scores else 0.0

        vector_hits = {}
        if self.db is not None and query:
            vec_query = self._get_embedding(query)
            if vec_query:
                try:
                    table = self.db.open_table("memories")
                    results = table.search(vec_query).metric("cosine").limit(top_k * 2).to_list()
                    for r in results:
                        dist = r.get("_distance", 1.0)
                        sim = 1.0 - dist
                        vector_hits[r["id"]] = max(0.0, sim)
                except (RuntimeError, ValueError) as e:
                    logger.debug("Vector search failed: %s", e)

        for idx, mem in enumerate(self.memories):
            # 1. Relevance
            # Default to keyword Jaccard + BM25 + vector
            text = mem.text or ""
            kw = [str(k).lower() for k in (mem.keywords or [])]
            mem_terms = set(self._bm25._tokenize(text)) | set(kw)

            if not mem_terms or not query_terms:
                keyword_score = 0.0
            else:
                intersection = query_terms.intersection(mem_terms)
                union = query_terms.union(mem_terms)
                keyword_score = len(intersection) / len(union) if union else 0.0

            bm25_raw = bm25_scores[idx] if idx < len(bm25_scores) else 0.0
            bm25_score = (bm25_raw / max_bm25) if max_bm25 > 0 else 0.0

            # Apply Vector Score if available
            vector_score = vector_hits.get(mem.id, 0.0)

            # Hybrid Score: best available relevance signal
            relevance = max(keyword_score, bm25_score, vector_score)

            # 2. Recency (Step-based exponential decay)
            delta_step = max(0, current_step - mem.step)
            recency = math.exp(-delta_step / max(decay_tau, 1e-6))

            # 3. Importance (Normalized 0-1)
            imp_val = mem.importance
            if not isinstance(imp_val, (int, float)):
                imp_val = 5
            importance = min(max(imp_val, 1), 10) / 10.0

            score = (
                (resolved_weights.get("rel", 0.5) * relevance)
                + (resolved_weights.get("rec", 0.3) * recency)
                + (resolved_weights.get("imp", 0.2) * importance)
            )

            scored_memories.append((score, mem))

        # Sort by score descending
        scored_memories.sort(key=lambda x: x[0], reverse=True)

        # Apply Pruning & Diversity
        pruned_items = self._prune_candidates([m for s, m in scored_memories], top_k)

        # Map back to scores
        score_map = {m.id: s for s, m in scored_memories}
        final_with_scores = [(item, score_map.get(item.id, 0.0)) for item in pruned_items]

        if self._cache:
            self._cache.set(query, current_step, top_k, resolved_weights, final_with_scores)

        if return_scores:
            return final_with_scores
        return [item for item, _score in final_with_scores]

    def _prune_candidates(self, candidates: list[MemoryItem], limit: int) -> list[MemoryItem]:
        """
        Applies diversity rules:
        - Max 5 'error' items
        - Max 3 'info' items
        - Max 3 'success' items
        """
        counts = {"error": 0, "info": 0, "success": 0, "warning": 0, "debug": 0}
        limits = {"error": 5, "info": 3, "success": 3, "warning": 2, "debug": 1}

        final_list: list[MemoryItem] = []
        hashes_seen: set[str] = set()

        for mem in candidates:
            if len(final_list) >= limit:
                break

            # Dedup
            if mem.hash in hashes_seen:
                continue

            # Diversity check
            kind = mem.kind.lower()
            if kind in limits:
                if counts[kind] >= limits[kind]:
                    continue
                counts[kind] += 1
            else:
                # Default limit for unknown kinds
                if counts.get("other", 0) >= 2:
                    continue
                counts["other"] = counts.get("other", 0) + 1

            hashes_seen.add(mem.hash)
            final_list.append(mem)

        return final_list

    def retrieve_recent(self, since_step: int) -> list[MemoryItem]:
        """Retrieves memories created after the given step."""
        return [m for m in self.memories if m.step > since_step]

    def count_recent_errors(self, since_step: int) -> int:
        """Counts error memories created after the given step."""
        return sum(1 for m in self.memories if m.step > since_step and m.kind == "error")
