"""Holographic benchmark executor dispatch table and case selection.

The ``EXECUTORS`` dispatch dict is assembled at import time by importing
each domain module under ``cases/`` and mapping every case ID to its
executor function. This mirrors the historical assembly that lived in
``runner.py`` and must stay fully populated and identical to before.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.benchmark.holographic.cases.cassette_http import (
    _exec_tc_cm_001,
    _exec_tc_cm_002,
    _exec_tc_cm_003,
    _exec_tc_cm_004,
)
from polaris.kernelone.benchmark.holographic.cases.cognitive import (
    _exec_tc_cog_001,
    _exec_tc_cog_002,
    _exec_tc_cog_003,
    _exec_tc_cog_004,
)
from polaris.kernelone.benchmark.holographic.cases.knowledge_pipeline import (
    _exec_tc_tc_001,
    _exec_tc_tc_002,
    _exec_tc_tc_003,
    _exec_tc_tc_004,
)
from polaris.kernelone.benchmark.holographic.cases.neural_syndicate import (
    _exec_tc_ns_001,
    _exec_tc_ns_002,
    _exec_tc_ns_003,
    _exec_tc_ns_004,
)
from polaris.kernelone.benchmark.holographic.cases.phx import (
    _exec_tc_phx_001,
    _exec_tc_phx_002,
    _exec_tc_phx_003,
    _exec_tc_phx_004,
    _exec_tc_phx_005,
)
from polaris.kernelone.benchmark.holographic.cases.platform_services import (
    _exec_tc_ag_001,
    _exec_tc_ag_002,
    _exec_tc_ag_003,
    _exec_tc_au_001,
    _exec_tc_au_002,
    _exec_tc_au_003,
    _exec_tc_ks_001,
    _exec_tc_ks_002,
    _exec_tc_ks_003,
    _exec_tc_ml_001,
    _exec_tc_ml_002,
    _exec_tc_qm_001,
    _exec_tc_qm_002,
    _exec_tc_qm_003,
    _exec_tc_ss_001,
    _exec_tc_ss_002,
    _exec_tc_ss_003,
)
from polaris.kernelone.benchmark.holographic.cases.streaming_parser import (
    _exec_tc_er_001,
    _exec_tc_er_002,
    _exec_tc_er_003,
    _exec_tc_er_004,
    _exec_tc_nw_001,
    _exec_tc_nw_002,
    _exec_tc_nw_003,
    _exec_tc_nw_004,
)
from polaris.kernelone.benchmark.holographic.cases.workflow_saga import (
    _exec_tc_chr_001,
    _exec_tc_chr_002,
    _exec_tc_chr_003,
)
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_registry import HOLOGRAPHIC_CASES

EXECUTORS: dict[str, Any] = {
    "TC-PHX-001": _exec_tc_phx_001,
    "TC-PHX-002": _exec_tc_phx_002,
    "TC-PHX-003": _exec_tc_phx_003,
    "TC-PHX-004": _exec_tc_phx_004,
    "TC-PHX-005": _exec_tc_phx_005,
    "TC-NS-001": _exec_tc_ns_001,
    "TC-NS-002": _exec_tc_ns_002,
    "TC-NS-003": _exec_tc_ns_003,
    "TC-NS-004": _exec_tc_ns_004,
    "TC-CHR-001": _exec_tc_chr_001,
    "TC-CHR-002": _exec_tc_chr_002,
    "TC-CHR-003": _exec_tc_chr_003,
    "TC-TC-001": _exec_tc_tc_001,
    "TC-TC-002": _exec_tc_tc_002,
    "TC-TC-003": _exec_tc_tc_003,
    "TC-TC-004": _exec_tc_tc_004,
    "TC-NW-001": _exec_tc_nw_001,
    "TC-NW-002": _exec_tc_nw_002,
    "TC-NW-003": _exec_tc_nw_003,
    "TC-NW-004": _exec_tc_nw_004,
    "TC-ER-001": _exec_tc_er_001,
    "TC-ER-002": _exec_tc_er_002,
    "TC-ER-003": _exec_tc_er_003,
    "TC-ER-004": _exec_tc_er_004,
    "TC-CM-001": _exec_tc_cm_001,
    "TC-CM-002": _exec_tc_cm_002,
    "TC-CM-003": _exec_tc_cm_003,
    "TC-CM-004": _exec_tc_cm_004,
    "TC-AU-001": _exec_tc_au_001,
    "TC-AU-002": _exec_tc_au_002,
    "TC-AU-003": _exec_tc_au_003,
    "TC-AG-001": _exec_tc_ag_001,
    "TC-AG-002": _exec_tc_ag_002,
    "TC-AG-003": _exec_tc_ag_003,
    "TC-SS-001": _exec_tc_ss_001,
    "TC-SS-002": _exec_tc_ss_002,
    "TC-SS-003": _exec_tc_ss_003,
    "TC-KS-001": _exec_tc_ks_001,
    "TC-KS-002": _exec_tc_ks_002,
    "TC-KS-003": _exec_tc_ks_003,
    "TC-ML-001": _exec_tc_ml_001,
    "TC-ML-002": _exec_tc_ml_002,
    "TC-QM-001": _exec_tc_qm_001,
    "TC-QM-002": _exec_tc_qm_002,
    "TC-QM-003": _exec_tc_qm_003,
    "TC-COG-001": _exec_tc_cog_001,
    "TC-COG-002": _exec_tc_cog_002,
    "TC-COG-003": _exec_tc_cog_003,
    "TC-COG-004": _exec_tc_cog_004,
}


def _select_cases(case_ids: set[str] | None) -> list[HolographicCase]:
    if case_ids is None:
        return list(HOLOGRAPHIC_CASES)
    return [case for case in HOLOGRAPHIC_CASES if case.case_id in case_ids]
