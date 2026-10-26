"""Regression tests for the ContextOS -> roles.kernel metrics reverse dependency.

Context (2026-10-27, wave-2 refactor batch ``employee-2-context-metrics``):

``polaris/kernelone/context/context_os/models.py`` used to carry a private
``_ContextOSProjectionV1`` dataclass whose ``compress()`` implementation
imported ``polaris.cells.roles.kernel.public.get_dead_loop_metrics`` and
called ``record_emergency_compaction(event_count)``.  That made the
KernelOne foundation depend on a business Cell (a foundation -> Cell reverse
dependency), and it was the only place in the repository that recorded that
metric.

Reachability was falsified before anything was removed:

* ``polaris.kernelone.context.context_os.models`` had no importer anywhere in
  the main tree (only the module's own migration comment, documentation, and
  the graph catalog's ``current_modules`` list referenced it);
* ``_ContextOSProjectionV1`` was referenced only by itself - no constructor
  call site, no test, no persisted-runtime reader;
* the public name ``ContextOSProjection`` resolved to
  ``models_v2.ContextOSProjectionV2`` both in ``context_os/__init__.py`` and in
  the deprecated module itself, so the *live* compression path never executed
  the emergency-compaction branch at all.

This module therefore only covers the two properties that this batch owns and
that belong to the KernelOne side:

1. A cold import of the foundation module loads no Cell module.
2. The historical public aliases of the deprecated module still resolve to the
   V2 model objects and are still usable, including for a snapshot restore.

The roles.kernel metric owner is unchanged and stays covered by its own
existing tests.  The architecture fence that removes the module from the
deprecated-shim allowlist already asserts the absence of the reverse import,
so this file deliberately does not re-implement a source-text scan, and the
unchanged V2 compression algorithm is not pinned here either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# polaris/kernelone/context/context_os/tests/<this file> -> src/backend
BACKEND_ROOT = Path(__file__).resolve().parents[5]


def test_importing_deprecated_models_module_loads_no_cell_module() -> None:
    """Cold-importing the foundation module must not pull in any Cell.

    The reverse dependency used to be function-local, so a cold import was
    already clean; this pins that property so it cannot regress into an eager
    import while the module is being reshaped.
    """
    script = (
        "import sys, warnings\n"
        "warnings.simplefilter('ignore')\n"
        "import polaris.kernelone.context.context_os.models\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('polaris.cells'))))\n"
    )
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(BACKEND_ROOT)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(BACKEND_ROOT),
        env=child_env,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", (
        f"cold import of the ContextOS foundation module loaded Cell modules: {completed.stdout.strip()}"
    )


def test_historical_public_aliases_still_bind_the_v2_objects() -> None:
    """The historical public aliases still resolve to the V2 model objects.

    Consumers import ``ContextOSSnapshot`` / ``ContextOSProjection`` from the
    deprecated module; those names must keep resolving to the V2 classes, so
    the removal of the v1 dataclasses does not change the public contract.
    """
    import polaris.kernelone.context.context_os.models as deprecated_models
    from polaris.kernelone.context.context_os.models_v2 import (
        ContextOSProjectionV2,
        ContextOSSnapshotV2,
        TranscriptEventV2,
    )

    assert deprecated_models.ContextOSProjection is ContextOSProjectionV2
    assert deprecated_models.ContextOSSnapshot is ContextOSSnapshotV2
    assert deprecated_models.TranscriptEvent is TranscriptEventV2

    for name in deprecated_models.__all__:
        assert hasattr(deprecated_models, name), f"missing public alias: {name}"


def test_historical_public_aliases_remain_usable_for_snapshot_restore() -> None:
    """A snapshot still round-trips through the historical public aliases.

    This exercises the restore path through the deprecated module's public
    names, which is how existing consumers reach the model family.
    """
    import polaris.kernelone.context.context_os.models as deprecated_models

    event = deprecated_models.TranscriptEvent(
        event_id="evt_001",
        sequence=0,
        role="user",
        kind="message",
        route="CLEAR",
        content="hello",
        source_turns=("t0",),
    )
    snapshot = deprecated_models.ContextOSSnapshot(transcript_log=(event,))

    restored = deprecated_models.ContextOSSnapshot.from_mapping(snapshot.to_dict())

    assert restored is not None
    assert len(restored.transcript_log) == 1
    assert restored.transcript_log[0].event_id == "evt_001"
    assert restored.transcript_log[0].content == "hello"


def test_deprecated_policy_and_enum_behaviour_is_unchanged() -> None:
    """The deprecated policy and enum keep their observable behaviour."""
    import polaris.kernelone.context.context_os.models as deprecated_models
    from polaris.kernelone.context.context_os.models_v2 import DialogAct as CanonicalDialogAct

    policy = deprecated_models.StateFirstContextOSPolicy()
    assert policy.model_context_window == 128_000
    assert policy.max_active_window_messages == 18

    assert deprecated_models.DialogAct.AFFIRM == "affirm"
    assert deprecated_models.DialogAct.UNKNOWN == "unknown"
    assert deprecated_models.DialogAct.is_high_priority("affirm") is True
    assert deprecated_models.DialogAct.is_high_priority("noise") is False
    assert deprecated_models.DialogAct.values() == tuple(member.value for member in CanonicalDialogAct)
