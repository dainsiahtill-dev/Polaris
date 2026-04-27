"""
Polaris Protocol (HP) - Internal implementation.

The internal module re-exports the full public surface (types + PolicyRuntime)
from public/contracts.py and adds internal-only helpers that live exclusively
in this subtree.  No internal module may be imported by a public module.

Phase semantics:
  1. hp_start_run  (Define goals)  - goal / acceptance criteria
  2. hp_create_blueprint (制图) - mode, budget
  3. hp_record_approval (Discussion) - policy gate / budget approval
  4. hp_create_snapshot (快照) - backup (skipped for S1 fast-lane)
  5. hp_allow_implementation (Execute) - authorize LLM code generation
  6. hp_run_verify  (Verify)  - Director self-check (compile/type lint)
  7. hp_finalize_run (Finalize) - record results

See public/contracts.py for the authoritative phase documentation.
"""

from __future__ import annotations

# Re-export the public surface so internal callers can `from
# polaris.cells.policy.protocol.internal.contracts import PolicyRuntime` without
# knowing the public path.  The public module is the canonical source of
# truth; this module exists for backward compatibility and internal helpers.
from polaris.cells.policy.protocol.public.contracts import (
    HP_PIPELINE,
    PolicyContractError,
    PolicyRunState,
    PolicyRuntime,
)

__all__ = [
    "HP_PIPELINE",
    "PolicyContractError",
    "PolicyRunState",
    "PolicyRuntime",
]
