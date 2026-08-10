"""Aggregated factory-bench helper surface (private).

Pulls the full cli namespace (which transitively includes all concern modules).
"""

from __future__ import annotations

# ruff: noqa: E402
# mypy: ignore-errors


def _pull_namespace(module: object) -> None:
    g = globals()
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        g[key] = value


from scripts.factory_bench._bench_lib import cli as _cli

_pull_namespace(_cli)
del _cli
