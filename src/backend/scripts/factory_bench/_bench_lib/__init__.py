"""Private factory-bench helper package.

Internal implementation support for run_factory_bench.py. Not a public API.

Concern modules (real implementations, dependency-ordered):
constants → workspace → catalog → artifacts → session → gates → chain → cli

impl.py re-exports the full cli namespace for a single import surface.
"""

from __future__ import annotations

from scripts.factory_bench._bench_lib import impl as impl

__all__ = ["impl"]
