"""PM task quality-gate package (lossless decomposition of ``task_quality_gate``).

The historical single-file ``task_quality_gate`` module was decomposed,
behavior-preserving, into three same-cell submodules forming an acyclic
``primitives -> domain_contracts -> gate`` dependency DAG:

* :mod:`.primitives` - domain-agnostic, dependency-free helpers.
* :mod:`.domain_contracts` - ALL CLAUDE.md §8 game/card3d project-specific
  domain-contract behavior, isolated behind the ``_domain_contracts_enabled``
  gate (env ``KERNELONE_PM_DOMAIN_CONTRACTS``, default-enabled).
* :mod:`.gate` - the generic, platform-clean quality gate entry points.

The original import path
``polaris.cells.orchestration.pm_planning.internal.task_quality_gate`` remains a
thin re-export shim over these modules and is the canonical public surface.
"""

from __future__ import annotations
