import statistics
import time
from typing import Callable

import pytest


class PerformanceBenchmark:
    def __init__(self, iterations: int = 100):
        self.iterations = iterations

    def measure(self, func: Callable, *args) -> dict:
        times: list[float] = []
        for _ in range(self.iterations):
            start = time.perf_counter()
            func(*args)
            times.append(time.perf_counter() - start)

        times.sort()
        return {
            'min': min(times),
            'max': max(times),
            'mean': statistics.mean(times),
            'p50': times[int(len(times) * 0.5)],
            'p95': times[int(len(times) * 0.95)],
            'p99': times[int(len(times) * 0.99)],
        }

@pytest.fixture
def benchmark():
    return PerformanceBenchmark()
