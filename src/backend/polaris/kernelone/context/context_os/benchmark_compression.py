"""Compression Benchmark - 压缩性能基准测试

ADR-0067: ContextOS 2.0 摘要策略选型

测试各摘要策略的性能和效果。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BenchmarkResult:
    """基准测试结果"""

    strategy: str
    content_type: str
    original_length: int
    compressed_length: int
    compression_ratio: float
    duration_ms: float
    quality_score: float | None
    success: bool


@dataclass
class BenchmarkReport:
    """基准测试报告"""

    total_tests: int = 0
    successful_tests: int = 0
    failed_tests: int = 0
    total_duration_ms: float = 0.0
    avg_compression_ratio: float = 0.0
    results: list[BenchmarkResult] | None = None


class CompressionBenchmark:
    """压缩基准测试

    测试各策略在不同场景下的表现。

    Example:
        ```python
        benchmark = CompressionBenchmark()

        # 添加测试用例
        benchmark.add_test_case(
            content="..." * 100,
            content_type="code",
            max_tokens=500,
        )

        # 运行基准测试
        report = benchmark.run()

        # 打印报告
        print(report)
        ```
    """

    def __init__(self) -> None:
        """初始化基准测试"""
        self._test_cases: list[dict[str, Any]] = []

    def add_test_case(
        self,
        content: str,
        content_type: str,
        max_tokens: int,
        expected_min_ratio: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """添加测试用例

        Args:
            content: 测试内容
            content_type: 内容类型
            max_tokens: 最大 token 数
            expected_min_ratio: 期望的最小压缩比
            metadata: 额外元数据
        """
        self._test_cases.append(
            {
                "content": content,
                "content_type": content_type,
                "max_tokens": max_tokens,
                "expected_min_ratio": expected_min_ratio,
                "metadata": metadata or {},
            }
        )

    def run(
        self,
        strategies: list[str] | None = None,
    ) -> BenchmarkReport:
        """运行基准测试

        Args:
            strategies: 要测试的策略列表，None 则测试所有

        Returns:
            基准测试报告
        """
        from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

        report = BenchmarkReport()
        report.results = []

        summarizer = TieredSummarizer(enable_tracking=False)
        available = summarizer.get_available_strategies()

        for test_case in self._test_cases:
            content = test_case["content"]
            content_type = test_case["content_type"]
            max_tokens = test_case["max_tokens"]

            for strategy in available:
                if strategies and strategy.name not in strategies:
                    continue

                start_time = time.time()
                success = True
                compressed = content
                compression_ratio = 1.0
                duration_ms = 0.0
                quality_score: float | None = None

                try:
                    start_time = time.time()
                    compressed = summarizer.summarize(
                        content=content,
                        max_tokens=max_tokens,
                        content_type=content_type,
                        force_strategy=strategy,
                    )
                    duration_ms = (time.time() - start_time) * 1000

                    # 计算压缩比
                    original_len = len(content)
                    compressed_len = len(compressed)
                    compression_ratio = compressed_len / max(original_len, 1)

                    # 计算质量评分 (简单版)
                    quality_score = self._quick_quality_score(
                        original=content,
                        compressed=compressed,
                        content_type=content_type,
                    )

                except (RuntimeError, ValueError) as e:
                    logger.debug(f"Benchmark failed for {strategy.name}: {e}")
                    success = False
                    duration_ms = (time.time() - start_time) * 1000

                result = BenchmarkResult(
                    strategy=strategy.name,
                    content_type=content_type,
                    original_length=len(content),
                    compressed_length=len(compressed),
                    compression_ratio=compression_ratio,
                    duration_ms=duration_ms,
                    quality_score=quality_score,
                    success=success,
                )

                report.results.append(result)
                report.total_tests += 1
                if success:
                    report.successful_tests += 1
                else:
                    report.failed_tests += 1
                report.total_duration_ms += duration_ms

        # 计算平均压缩比
        if report.results:
            report.avg_compression_ratio = sum(r.compression_ratio for r in report.results) / len(report.results)

        return report

    def _quick_quality_score(
        self,
        original: str,
        compressed: str,
        content_type: str,
    ) -> float:
        """快速质量评分

        简化的质量评估，不依赖外部模型。
        """
        # 基础评分: 压缩效果
        original_len = len(original)
        compressed_len = len(compressed)

        if original_len == 0:
            return 0.0

        compression_ratio = compressed_len / original_len

        # 压缩率评分 (0-50)
        if compression_ratio >= 1.0:
            ratio_score: float = 0.0
        elif compression_ratio >= 0.5:
            ratio_score = 50.0
        else:
            ratio_score = 50.0 + (50.0 * (0.5 - compression_ratio) / 0.5)

        # 关键词保留评分 (0-30)
        keywords = {"error", "exception", "failed", "important", "critical"}
        original_has = sum(1 for kw in keywords if kw in original.lower())
        compressed_has = sum(1 for kw in keywords if kw in compressed.lower())

        keyword_score: float = 30.0 if original_has == 0 else 30.0 * (compressed_has / original_has)

        # 长度合理性评分 (0-20)
        length_score = 20 if compression_ratio < 0.9 else 0

        return ratio_score + keyword_score + length_score

    def run_quick_benchmark(
        self,
        content: str,
        content_type: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        """运行快速基准测试

        测试所有可用策略对单个内容的效果。

        Args:
            content: 测试内容
            content_type: 内容类型
            max_tokens: 最大 token 数

        Returns:
            快速基准测试结果
        """
        from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

        summarizer = TieredSummarizer(enable_tracking=False)
        available = summarizer.get_available_strategies()

        results: dict[str, dict[str, Any]] = {}
        original_len = len(content)

        for strategy in available:
            start_time = time.time()
            compressed = content

            try:
                start_time = time.time()
                compressed = summarizer.summarize(
                    content=content,
                    max_tokens=max_tokens,
                    content_type=content_type,
                    force_strategy=strategy,
                )
                duration_ms = (time.time() - start_time) * 1000

                compression_ratio = len(compressed) / max(original_len, 1)

                results[strategy.name] = {
                    "success": True,
                    "compressed_length": len(compressed),
                    "compression_ratio": compression_ratio,
                    "duration_ms": duration_ms,
                    "quality_score": self._quick_quality_score(
                        original=content,
                        compressed=compressed,
                        content_type=content_type,
                    ),
                }

            except (RuntimeError, ValueError) as e:
                duration_ms = (time.time() - start_time) * 1000
                results[strategy.name] = {
                    "success": False,
                    "error": str(e),
                    "duration_ms": duration_ms,
                }

        return {
            "content_type": content_type,
            "original_length": original_len,
            "max_tokens": max_tokens,
            "results": results,
        }

    def generate_report(self, report: BenchmarkReport) -> str:
        """生成文本报告

        Args:
            report: 基准测试报告

        Returns:
            格式化的文本报告
        """
        lines = [
            "=" * 60,
            "Compression Benchmark Report",
            "=" * 60,
            f"Total Tests: {report.total_tests}",
            f"Successful: {report.successful_tests}",
            f"Failed: {report.failed_tests}",
            f"Avg Compression Ratio: {report.avg_compression_ratio:.2%}",
            f"Total Duration: {report.total_duration_ms:.2f}ms",
            "",
            "-" * 60,
            "Results:",
            "-" * 60,
        ]

        if report.results:
            for result in report.results:
                status = "PASS" if result.success else "FAIL"
                lines.append(
                    f"[{status}] {result.strategy} ({result.content_type}): "
                    f"{result.compression_ratio:.1%} in {result.duration_ms:.1f}ms"
                )

        lines.append("=" * 60)
        return "\n".join(lines)


# 标准测试用例
STANDARD_TEST_CASES: dict[str, dict[str, Any]] = {
    "short_code": {
        "content": '''
def hello():
    """Say hello."""
    print("Hello, World!")
    return True

def goodbye():
    """Say goodbye."""
    print("Goodbye, World!")
    return False
'''.strip(),
        "content_type": "code",
        "max_tokens": 100,
    },
    "long_code": {
        "content": '''
class MyClass:
    def __init__(self):
        self.value = 42
        self.data = []

    def process(self, item):
        """Process an item."""
        if item is None:
            raise ValueError("Item cannot be None")
        self.data.append(item)
        return self.value * len(self.data)

    def get_data(self):
        """Get all data."""
        return self.data.copy()

    def clear(self):
        """Clear all data."""
        self.data.clear()
        self.value = 0

def create_processor():
    """Create a new processor."""
    return MyClass()
'''.strip()
        * 5,  # Repeat to make it longer
        "content_type": "code",
        "max_tokens": 300,
    },
    "log_with_errors": {
        "content": """
[2024-01-01 10:00:00] INFO: Application started
[2024-01-01 10:00:01] DEBUG: Loading configuration
[2024-01-01 10:00:02] INFO: Connected to database
[2024-01-01 10:00:03] ERROR: Connection timeout to external API
[2024-01-01 10:00:04] ERROR: Failed to fetch user data
[2024-01-01 10:00:05] WARNING: Retrying connection
[2024-01-01 10:00:06] INFO: Connection restored
[2024-01-01 10:00:07] DEBUG: Processing request
[2024-01-01 10:00:08] INFO: Request completed successfully
[2024-01-01 10:00:09] DEBUG: Cleanup started
[2024-01-01 10:00:10] INFO: Application shutdown
""".strip(),
        "content_type": "log",
        "max_tokens": 200,
    },
    "json_data": {
        "content": """
{
    "users": [
        {"id": 1, "name": "Alice", "email": "alice@example.com"},
        {"id": 2, "name": "Bob", "email": "bob@example.com"},
        {"id": 3, "name": "Charlie", "email": "charlie@example.com"}
    ],
    "metadata": {
        "total": 3,
        "page": 1,
        "per_page": 10
    }
}
""".strip(),
        "content_type": "json",
        "max_tokens": 150,
    },
}


def run_standard_benchmark() -> BenchmarkReport:
    """运行标准基准测试

    Returns:
        基准测试报告
    """
    benchmark = CompressionBenchmark()

    for name, test_case in STANDARD_TEST_CASES.items():
        benchmark.add_test_case(
            content=test_case["content"],
            content_type=test_case["content_type"],
            max_tokens=test_case["max_tokens"],
            metadata={"test_case_name": name},
        )

    return benchmark.run()
