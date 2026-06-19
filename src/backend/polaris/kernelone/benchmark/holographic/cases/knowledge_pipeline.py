"""Knowledge-pipeline benchmark executors (TC-TC-001..004).

Semantic chunking boundary retention, document-pipeline parallelism,
and idempotent vector-store hashing / tombstone recall.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import time
from pathlib import Path
from typing import Any

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import (
    BaseExtractor,
    ExtractionOptions,
)
from polaris.kernelone.akashic.knowledge_pipeline.extractors.extractor_registry import (
    ExtractorRegistry,
)
from polaris.kernelone.akashic.knowledge_pipeline.idempotent_vector_store import (
    IdempotentVectorStore,
)
from polaris.kernelone.akashic.knowledge_pipeline.pipeline import (
    DocumentPipeline,
    PipelineConfig,
)
from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    DocumentInput,
    EnrichedChunk,
    ExtractedFragment,
    SemanticChunk,
)
from polaris.kernelone.akashic.knowledge_pipeline.semantic_chunker import (
    SemanticChunker,
)
from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory
from polaris.kernelone.benchmark.holographic.stats import (
    _boundary_retention,
    _chunk_ranges_fixed_80,
    _chunk_ranges_from_semantic,
    _perf_ms,
    _python_block_ranges,
    _token_similarity,
)
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples


def _synthetic_python_module(file_index: int, function_count: int = 20) -> str:
    lines: list[str] = [
        f"class SyntheticClass{file_index}:",
        "    def __init__(self, base):",
        "        self.base = base",
        "",
    ]
    for index in range(function_count):
        if index % 5 == 0:
            lines.extend(
                [
                    f"class Group{file_index}_{index}:",
                    "    def __init__(self):",
                    "        self.value = 0",
                    "",
                ]
            )
        lines.extend(
            [
                f"def function_{file_index}_{index}(value):",
                "    total = value",
                "    for step in range(5):",
                "        total += step",
                "    if total % 2 == 0:",
                "        total += 3",
                "    else:",
                "        total -= 1",
                "    return total",
                "",
            ]
        )
    return "\n".join(lines)


async def _exec_tc_tc_001(case: HolographicCase) -> dict[str, float]:
    file_count = max(100, case.min_samples)
    semantic_chunker = SemanticChunker(chunk_target_chars=100_000, chunk_min_chars=64, boundary_threshold=0.4)
    semantic_function_rates: list[float] = []
    semantic_class_rates: list[float] = []
    fixed_function_rates: list[float] = []
    fixed_class_rates: list[float] = []
    semantic_similarities: list[float] = []

    for index in range(file_count):
        source = _synthetic_python_module(index)
        lines = source.splitlines()
        function_blocks = _python_block_ranges(source, block_type="function")
        class_blocks = _python_block_ranges(source, block_type="class")

        semantic_chunks = semantic_chunker.chunk(source, source_hint="python")
        semantic_ranges = _chunk_ranges_from_semantic(semantic_chunks)
        fixed_ranges = _chunk_ranges_fixed_80(len(lines))

        semantic_function_rates.append(_boundary_retention(function_blocks, semantic_ranges))
        semantic_class_rates.append(_boundary_retention(class_blocks, semantic_ranges))
        fixed_function_rates.append(_boundary_retention(function_blocks, fixed_ranges))
        fixed_class_rates.append(_boundary_retention(class_blocks, fixed_ranges))

        if len(semantic_chunks) < 2:
            semantic_similarities.append(1.0)
        else:
            for left, right in itertools.pairwise(semantic_chunks):
                semantic_similarities.append(_token_similarity(left.text, right.text))

    semantic_fn_stats = summarize_samples(semantic_function_rates, warmup_rounds=case.warmup_rounds)
    semantic_cls_stats = summarize_samples(semantic_class_rates, warmup_rounds=case.warmup_rounds)
    fixed_fn_stats = summarize_samples(fixed_function_rates, warmup_rounds=case.warmup_rounds)
    fixed_cls_stats = summarize_samples(fixed_class_rates, warmup_rounds=case.warmup_rounds)
    similarity_stats = summarize_samples(semantic_similarities, warmup_rounds=case.warmup_rounds)

    return {
        "function_boundary_percent": semantic_fn_stats.mean,
        "class_boundary_percent": semantic_cls_stats.mean,
        "fixed_function_boundary_percent": fixed_fn_stats.mean,
        "fixed_class_boundary_percent": fixed_cls_stats.mean,
        "semantic_similarity_p50": similarity_stats.p50,
    }


class _SyntheticExtractor(BaseExtractor):
    SUPPORTED_MIME_TYPES: tuple[str, ...] = ("text/plain",)

    async def extract(self, doc: DocumentInput) -> list[ExtractedFragment]:
        await asyncio.sleep(0.0004)
        return await super().extract(doc)

    def _do_extract(
        self,
        text: str,
        options: ExtractionOptions,
    ) -> list[ExtractedFragment]:
        _ = options
        return [
            ExtractedFragment(
                text=text,
                line_start=1,
                line_end=max(1, len(text.splitlines())),
                mime_type=self.SUPPORTED_MIME_TYPES[0],
                metadata={},
            )
        ]


class _SyntheticChunker:
    def chunk(self, text: str, *, source_hint: str = "auto") -> list[SemanticChunk]:
        return [
            SemanticChunk(
                chunk_id=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                text=text,
                line_start=1,
                line_end=max(1, len(text.splitlines())),
                boundary_score=0.8,
                semantic_tags=("synthetic",),
                source_hint=source_hint,
            )
        ]


class _SyntheticEnricher:
    def enrich(self, chunk: SemanticChunk, source_file: str) -> EnrichedChunk:
        return EnrichedChunk(
            chunk=chunk,
            content_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()[:32],
            importance=5,
            source_file=source_file,
            metadata={"source_file": source_file, "semantic_tags": list(chunk.semantic_tags)},
        )


class _SyntheticEmbeddingComputer:
    async def compute_batch(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        await asyncio.sleep(0.0004)
        return [[float(len(text) % 17), 0.5, 1.5, 2.5] for text in texts]

    def get_stats(self) -> dict[str, Any]:
        return {"model": "synthetic", "dimension": 4}


class _SyntheticVectorStore:
    def __init__(self) -> None:
        self._items: list[str] = []

    async def add(self, text: str, *, metadata: dict[str, Any] | None = None, importance: int = 5) -> str:
        await asyncio.sleep(0.0004)
        item_id = f"mem-{len(self._items)}"
        self._items.append(item_id)
        _ = text, metadata, importance
        return item_id

    async def delete(self, memory_id: str) -> bool:
        if memory_id in self._items:
            self._items.remove(memory_id)
            return True
        return False

    async def search(self, query: str, *, top_k: int = 10, min_importance: int = 1) -> list[tuple[str, float]]:
        _ = query, min_importance
        return [(memory_id, 1.0) for memory_id in self._items[:top_k]]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        if memory_id in self._items:
            return {"memory_id": memory_id}
        return None

    async def vacuum(self, max_age_days: int = 30) -> int:
        _ = max_age_days
        return 0

    def get_stats(self) -> dict[str, Any]:
        return {"stored_items": len(self._items)}


async def _exec_tc_tc_004(case: HolographicCase) -> dict[str, float | str]:
    doc_count = max(400, min(case.min_samples, 1000))
    documents = [
        DocumentInput(
            source=f"doc-{index}.txt",
            mime_type="text/plain",
            content=f"Document {index}\n" + ("payload " * 40),
            metadata={"index": index},
        )
        for index in range(doc_count)
    ]
    extractor = _SyntheticExtractor()
    extractor_registry = ExtractorRegistry()
    extractor_registry.register(extractor)
    vector_store = _SyntheticVectorStore()
    pipeline = DocumentPipeline(
        workspace=".",
        chunker=_SyntheticChunker(),
        enricher=_SyntheticEnricher(),
        embedding_computer=_SyntheticEmbeddingComputer(),
        vector_store=vector_store,
        extractor_registry=extractor_registry,
        config=PipelineConfig(max_concurrency=64, batch_size=64, workspace="."),
    )

    serial_start = time.perf_counter_ns()
    for document in documents:
        await pipeline._process_document(document)
    serial_total_ms = _perf_ms(serial_start)

    parallel_start = time.perf_counter_ns()
    parallel_results = await pipeline.run(documents)
    parallel_total_ms = _perf_ms(parallel_start)

    success_count = sum(1 for result in parallel_results if result.status in {"success", "partial"})
    throughput = doc_count / max(parallel_total_ms / 1000.0, 1e-9)
    speedup = serial_total_ms / max(parallel_total_ms, 1e-9)

    return {
        "pipeline_p99_ms": parallel_total_ms,
        "parallel_speedup": speedup,
        "pipeline_throughput_docs_s": throughput,
        "success_percent": (success_count / doc_count) * 100.0,
        "bottleneck_stage": "embedding_or_store",
    }


async def _exec_tc_tc_002(case: HolographicCase) -> dict[str, float]:
    with TempfileWorkspace() as memory_file:
        semantic = AkashicSemanticMemory(
            workspace=".",
            memory_file=str(memory_file),
            enable_vector_search=False,
        )
        store = IdempotentVectorStore(semantic)
        text = "benchmark-idempotent-text"
        hit_latencies_ms: list[float] = []
        for _ in range(100):
            begin = time.perf_counter_ns()
            await store.add(text, metadata={"case": case.case_id}, importance=5)
            hit_latencies_ms.append(_perf_ms(begin))
        line_count = 0
        with open(memory_file, encoding="utf-8") as handle:
            for _line in handle:
                line_count += 1
        results = await store.search("idempotent", top_k=10)
        stats = summarize_samples(hit_latencies_ms, warmup_rounds=case.warmup_rounds)
        return {
            "append_count": float(line_count),
            "search_hits": float(len(results)),
            "hash_lookup_p99_ms": stats.p99,
        }


async def _exec_tc_tc_003(case: HolographicCase) -> dict[str, float]:
    with TempfileWorkspace() as memory_file:
        semantic = AkashicSemanticMemory(
            workspace=".",
            memory_file=str(memory_file),
            enable_vector_search=False,
        )
        store = IdempotentVectorStore(semantic)
        ids: list[str] = []
        for index in range(100):
            memory_id = await store.add(f"doc-{index}", importance=5)
            ids.append(memory_id)
        deleted_ids = set(ids[:50])
        for memory_id in deleted_ids:
            await store.delete(memory_id)

        begin = time.perf_counter_ns()
        semantic_reloaded = AkashicSemanticMemory(
            workspace=".",
            memory_file=str(memory_file),
            enable_vector_search=False,
        )
        load_ms = _perf_ms(begin)
        revived = sum(1 for memory_id in deleted_ids if memory_id in semantic_reloaded._items)
        live_ids = set(ids[50:])
        recalled = sum(1 for memory_id in live_ids if memory_id in semantic_reloaded._items)
        return {
            "deleted_revival_percent": (revived / len(deleted_ids)) * 100.0,
            "survival_recall_percent": (recalled / len(live_ids)) * 100.0,
            "load_p99_ms": load_ms,
        }


class TempfileWorkspace:
    """Temporary JSONL path context manager for semantic-memory tests."""

    def __init__(self) -> None:
        self._path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        directory = Path(tempfile.mkdtemp(prefix="holo-bench-"))
        self._path = directory / "memory.jsonl"
        return self._path

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._path is None:
            return
        try:
            import shutil

            shutil.rmtree(self._path.parent, ignore_errors=True)
        except OSError:
            return
