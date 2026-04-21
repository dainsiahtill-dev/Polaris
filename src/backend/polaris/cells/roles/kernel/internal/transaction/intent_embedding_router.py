"""Embedding-based intent router — Phase 2 Hybrid Intent Routing.

Regex first, embedding fallback. Zero startup blocking.

Architecture:
- Background daemon thread pre-computes intent centroids at singleton creation.
- `classify()` is async: embedding call wrapped in asyncio.to_thread to avoid
  blocking the event loop (embedding port may perform HTTP I/O to Ollama).
- Pure CPU cosine similarity runs synchronously after embedding is obtained.
- If centroids not ready or embedding fails, returns None → caller falls back to regex.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading

from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.kernelone.llm.embedding import KernelEmbeddingPort, get_default_embedding_port

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent descriptions for embedding space anchoring.
# Each label gets multiple descriptions (CN + EN) to improve centroid quality.
# ---------------------------------------------------------------------------
INTENT_DESCRIPTIONS: dict[str, list[str]] = {
    "STRONG_MUTATION": [
        "修改代码 创建文件 删除函数 重写逻辑 实现功能 写入文件",
        "modify code create file delete function rewrite logic implement feature write file",
    ],
    "DEBUG_AND_FIX": [
        "修复bug 排查错误 解决异常 调试程序 定位问题",
        "debug crash fix bug troubleshoot error resolve exception investigate issue",
    ],
    "DEVOPS": [
        "安装依赖 配置环境 部署服务 构建项目 编译代码 打包发布",
        "install dependency configure environment deploy service build project compile package",
    ],
    "WEAK_MUTATION": [
        "重构代码 优化性能 格式化 清理代码 调整结构 改进代码",
        "refactor code optimize performance format cleanup polish restructure improve tune",
    ],
    "TESTING": [
        "运行测试 验证结果 执行pytest 断言检查 测试覆盖",
        "run tests verify results execute pytest assert check test coverage",
    ],
    "PLANNING": [
        "规划任务 拆解需求 设计架构 排期 制定蓝图 任务单",
        "plan task breakdown requirements design architecture blueprint roadmap epic story",
    ],
    "ANALYSIS_ONLY": [
        "分析代码 审查架构 解释逻辑 总结代码 阅读代码 了解项目",
        "analyze code review architecture explain logic summarize inspect audit walkthrough explore",
    ],
}

DEFAULT_SIMILARITY_THRESHOLD: float = 0.72


class IntentEmbeddingRouter:
    """Lazy-loading embedding intent router with background warmup.

    Designed for async contexts (event-loop friendly).  All blocking I/O
    (embedding API calls) is offloaded to threads via asyncio.to_thread.

    Usage:
        # Production — singleton with background warmup
        router = IntentEmbeddingRouter.default()

        # Testing — direct instantiation, manual warmup control
        router = IntentEmbeddingRouter(embedding_port=fake_port)
        router.start_background_warmup()
    """

    _default_instance: IntentEmbeddingRouter | None = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        embedding_port: KernelEmbeddingPort | None = None,
        config: TransactionConfig | None = None,
    ) -> None:
        self._embedding_port_override = embedding_port
        self._config = config or TransactionConfig()
        self._centroids: dict[str, list[float]] | None = None
        self._warmup_done = False
        self._threshold = self._config.intent_embedding_threshold
        self._warmup_lock = asyncio.Lock()

    def start_background_warmup(self) -> None:
        """Start background daemon thread for centroids warmup."""
        threading.Thread(target=self._warmup_centroids, daemon=True).start()

    @classmethod
    def default(cls) -> IntentEmbeddingRouter:
        """Get the global singleton instance with background warmup started."""
        if cls._default_instance is None:
            with cls._instance_lock:
                if cls._default_instance is None:
                    cls._default_instance = cls()
                    cls._default_instance.start_background_warmup()
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """Reset singleton — primarily for test isolation."""
        with cls._instance_lock:
            cls._default_instance = None

    def _resolve_embedding_port(self) -> KernelEmbeddingPort:
        if self._embedding_port_override is not None:
            return self._embedding_port_override
        return get_default_embedding_port()

    def _warmup_centroids(self) -> None:
        """Background thread: pre-compute all intent centroids."""
        try:
            port = self._resolve_embedding_port()
            centroids = self._compute_centroids(port)
            self._centroids = centroids if centroids else None
            if self._centroids:
                logger.info(
                    "IntentEmbeddingRouter centroids warmed up: %d intents",
                    len(self._centroids),
                )
        except RuntimeError as exc:
            if self._embedding_port_override is None and "Default KernelEmbeddingPort not set" in str(exc):
                logger.info("IntentEmbeddingRouter warmup skipped: embedding port not configured")
            else:
                logger.warning("IntentEmbeddingRouter centroid warmup failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("IntentEmbeddingRouter centroid warmup failed: %s", exc)
        finally:
            self._warmup_done = True

    def _compute_centroids(self, port: KernelEmbeddingPort) -> dict[str, list[float]]:
        centroids: dict[str, list[float]] = {}
        for label, descriptions in INTENT_DESCRIPTIONS.items():
            vectors: list[list[float]] = []
            for desc in descriptions:
                try:
                    vec = port.get_embedding(desc)
                    vectors.append(vec)
                except Exception:  # noqa: BLE001
                    logger.debug("Failed to embed description for %s", label, exc_info=True)
            if vectors:
                centroids[label] = self._average_vectors(vectors)
        return centroids

    @staticmethod
    def _average_vectors(vectors: list[list[float]]) -> list[float]:
        dim = len(vectors[0])
        count = len(vectors)
        return [sum(v[i] for v in vectors) / count for i in range(dim)]

    async def classify(self, message: str) -> str | None:
        """Classify message using embedding similarity.

        Returns the best matching intent label if similarity >= threshold,
        otherwise None (caller should fall back to regex).
        """
        if not self._config.intent_embedding_enabled:
            return None
        if self._centroids is None:
            return None
        try:
            vec = await self._embed_async(message)
        except Exception:  # noqa: BLE001
            logger.debug("Embedding call failed for intent classification", exc_info=True)
            return None
        best_label, best_score = self._find_best(vec)
        if best_score >= self._threshold:
            return best_label
        return None

    async def _embed_async(self, text: str) -> list[float]:
        port = self._resolve_embedding_port()
        return await asyncio.to_thread(port.get_embedding, text)

    def _find_best(self, vector: list[float]) -> tuple[str, float]:
        best_label = ""
        best_score = -1.0
        for label, centroid in (self._centroids or {}).items():
            score = _cosine_similarity(vector, centroid)
            if score > best_score:
                best_score = score
                best_label = label
        return best_label, best_score


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def classify_with_embedding_fallback(message: str, regex_intent: str) -> str:
    """Hybrid classifier: regex first, embedding resolves ambiguity.

    High-confidence regex results (strong mutation, debug, devops) are trusted
    directly. Weak or ambiguous signals get an embedding second opinion.
    """
    # High-confidence regex signals — zero-latency fast path.
    if regex_intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS"}:
        return regex_intent

    # Ambiguous / weak / unknown — ask embedding router.
    router = IntentEmbeddingRouter.default()
    emb_intent = await router.classify(message)
    if emb_intent is not None:
        return emb_intent
    return regex_intent
