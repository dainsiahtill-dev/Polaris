"""A009B1 Factory-owned fenced role-evidence cutoff authority.

This package is the lossless successor of the former
``factory_role_evidence_authority`` module. It re-exports every previously-public
symbol from the same import path so that
``import ...factory_role_evidence_authority`` and
``from ...factory_role_evidence_authority import X`` keep resolving identically
for all external importers.

This module freezes only the authority ledger boundary.  Source reconstruction
is deliberately injected and defaults to unavailable until A009B2; role/provider
binding remains absent until A009B3.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing names that
# were module-level attributes of the former ``factory_role_evidence_authority``
# module. Keeping them bound here preserves the exact importable attribute
# surface after the split (dir() oracle COUNT=75).
import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
    append_segmented_fact_event,
    ensure_segmented_fact_ledger,
    query_segmented_fact_events,
    query_segmented_fact_ledger_head,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRun, FactoryRunStatus
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    FactoryRoleEvidenceCutoffSourceHeadV1 as PublicFactoryRoleEvidenceCutoffSourceHeadV1,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
    role_final_request_policy,
)

from ._constants import (
    _ABSENT_STATE,
    _AUTHORITY_SOURCE,
    _AUTHORITY_STREAM_PREFIX,
    _FRAGMENT_ENCODING,
    _FRAGMENT_RAW_BYTES,
    _HASH_LENGTH,
    _LOCATOR_PATTERN,
    _MAX_CUTOFF_BODY_BYTES,
    _MAX_CUTOFF_FRAGMENTS,
    _MAX_REQUEST_FREEZES_PER_GRANT,
    _MAX_SOURCE_ITEMS_PER_SLOT,
    _MAX_SOURCE_ITEMS_TOTAL,
    _PRESENT_STATE,
    _STAGE_ROLE_AND_GRANT_CAP,
    FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
    FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
    FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
    FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA,
    FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA,
)
from ._models import (
    FactoryRoleEvidenceCutoffBodyV1,
    FactoryRoleEvidenceResolvedCutV1,
    FactoryRoleEvidenceSourceHeadV1,
    FactoryRoleEvidenceSourceItemV1,
    FactoryRoleEvidenceSourceSlotV1,
    FactoryRoleEvidenceStageAuthorityV1,
    _canonical_cutoff_body_bytes,
    _CutoffCommitManifest,
    _CutoffFragmentPayload,
    _decode_base64url,
    _encode_base64url,
    _fragment_cutoff_body,
    _request_authority_hash,
)
from ._port import FactoryRoleEvidenceAuthorityPort
from ._primitives import (
    _T,
    FactoryRoleEvidenceAuthorityError,
    _exact_mapping,
    _FactoryRoleEvidenceGrantState,
    _hash64,
    _locator,
    _non_negative_int,
    _positive_int,
    _text,
    factory_role_evidence_authority_stream,
)
from ._replay import (
    FactoryRoleEvidenceReplayCutoffV1,
    FactoryRoleEvidenceReplaySnapshotV1,
    _FactoryRoleEvidenceReplayScanReader,
    query_factory_role_evidence_replay_snapshot,
)
from ._source import (
    FactoryRoleEvidenceFactStream,
    FactoryRoleEvidenceSourceAuthority,
    UnavailableFactoryRoleEvidenceSourceAuthority,
    _AuthorityScan,
    _fragment_vector_hash,
    _PartialCutoff,
    _PublicFactoryRoleEvidenceFactStream,
    _StoredCutoff,
    _StoredFragment,
)

__all__ = [
    "FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE",
    "FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA",
    "FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA",
    "FactoryRoleEvidenceAuthorityError",
    "FactoryRoleEvidenceAuthorityPort",
    "FactoryRoleEvidenceCutoffBodyV1",
    "FactoryRoleEvidenceReplayCutoffV1",
    "FactoryRoleEvidenceReplaySnapshotV1",
    "FactoryRoleEvidenceResolvedCutV1",
    "FactoryRoleEvidenceSourceAuthority",
    "FactoryRoleEvidenceSourceHeadV1",
    "FactoryRoleEvidenceSourceItemV1",
    "FactoryRoleEvidenceSourceSlotV1",
    "FactoryRoleEvidenceStageAuthorityV1",
    "UnavailableFactoryRoleEvidenceSourceAuthority",
    "factory_role_evidence_authority_stream",
    "query_factory_role_evidence_replay_snapshot",
]
