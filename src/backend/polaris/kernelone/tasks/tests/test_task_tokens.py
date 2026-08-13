"""Canonical task-token normalizer (§9.5 SSoT).

The PM taskboard uses numeric IDs (``1, 2, 3``), CE blueprints use ``TASK-N``
prefixed IDs, and the orchestration layer uses ``task-0-director`` style IDs.
§9.5 mandates one canonical normalizer so cross-role task-id lookups compare
equal regardless of prefix/format. This regression pins the SSoT and the
parity between the two legacy delegating wrappers.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.tasks.task_tokens import normalize_task_token


class TestNormalizeTaskToken:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", "1"),
            ("TASK-1", "1"),
            ("task-1", "1"),
            ("task_1", "1"),
            ("Task-42", "42"),
            ("task-0-director", "0-director"),
            ("task_task-1", "1"),
            ("  TASK-7  ", "7"),
            ("", ""),
            (None, ""),
            (123, "123"),
        ],
    )
    def test_canonical_normalization(self, raw: object, expected: str) -> None:
        assert normalize_task_token(raw) == expected

    def test_legacy_wrappers_delegate_to_canonical(self) -> None:
        """The two pre-existing normalizers must produce identical results."""
        from polaris.cells.chief_engineer.blueprint.public.service._helpers import (
            _normalize_task_token as ce_normalize,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.task_boundary_filter import (
            normalize_task_token as cg_normalize,
        )

        samples = ["TASK-1", "task_2", "task-task-3", "4", "", "TASK-0-director"]
        for raw in samples:
            assert ce_normalize(raw) == cg_normalize(raw), f"divergence on {raw!r}"
            assert ce_normalize(raw) == normalize_task_token(raw)
