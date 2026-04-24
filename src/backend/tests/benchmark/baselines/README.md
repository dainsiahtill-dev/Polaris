# Benchmark Baselines

Performance baseline data storage for Polaris latency benchmarks.

## File Format

Each baseline file is a JSON document with the following structure:

```json
{
  "suite_name": "latency_baseline",
  "timestamp": "2026-04-24T10:00:00+00:00",
  "environment": {
    "platform": "win32",
    "python_version": "3.12.0"
  },
  "results": [
    {
      "name": "benchmark_name",
      "iterations": 100,
      "total_ms": 123.456,
      "min_ms": 1.0,
      "max_ms": 5.0,
      "mean_ms": 1.23,
      "p50_ms": 1.1,
      "p95_ms": 2.5,
      "p99_ms": 4.0,
      "stddev_ms": 0.5,
      "metadata": {}
    }
  ]
}
```

## Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `suite_name` | string | Identifier for the benchmark suite |
| `timestamp` | ISO 8601 | When the benchmark was run |
| `environment` | object | Runtime environment metadata |
| `results` | array | List of individual benchmark results |

### Result Fields

| Field | Unit | Description |
|-------|------|-------------|
| `name` | string | Benchmark identifier |
| `iterations` | int | Number of iterations measured |
| `total_ms` | ms | Sum of all iteration times |
| `min_ms` | ms | Fastest iteration |
| `max_ms` | ms | Slowest iteration |
| `mean_ms` | ms | Average iteration time |
| `p50_ms` | ms | Median (50th percentile) |
| `p95_ms` | ms | 95th percentile |
| `p99_ms` | ms | 99th percentile |
| `stddev_ms` | ms | Standard deviation |
| `metadata` | object | Additional context |

## Naming Convention

- `latency_baseline.json` - Core component latency baselines
- `latency_baseline_<date>.json` - Historical snapshots

## Usage

```python
from tests.benchmark.conftest import load_baseline, compare_with_baseline

# Load existing baseline
baseline = load_baseline("latency_baseline.json")

# Compare current results
comparison = compare_with_baseline(current_result, baseline)
if comparison["status"] == "FAIL":
    print(f"Regression: {comparison['regression_pct']}%")
```
