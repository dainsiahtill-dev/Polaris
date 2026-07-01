"""Namespace package for KernelOne LLM tool utility submodules.

Tool contracts live in :mod:`polaris.kernelone.llm.contracts`; execution
runtime exports live in :mod:`polaris.kernelone.llm.toolkit`. This package root
intentionally does not re-export those APIs, so imports stay explicit and the
submodules remain the only owners of message normalization, argument
normalization, and schema validation helpers.
"""

from __future__ import annotations

__all__: list[str] = []
