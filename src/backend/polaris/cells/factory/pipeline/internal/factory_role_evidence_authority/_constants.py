"""Constants and schema identifiers for factory role-evidence authority."""

from __future__ import annotations

import re

FACTORY_ROLE_EVIDENCE_SOURCE_CUT_SCHEMA = "polaris.factory_role_evidence_source_cut.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_BODY_SCHEMA = "polaris.factory_role_evidence_cutoff_body.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_SCHEMA = "polaris.factory_role_evidence_cutoff.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE = "factory.role_evidence_cutoff.issued"
FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_SCHEMA = "polaris.factory_role_evidence_cutoff_fragment.v1"
FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE = "factory.role_evidence_cutoff.fragment"
FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA = "polaris.factory_role_evidence_execution_authority.v1"
FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET = 32
FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY = "_factory_role_evidence_cutoff_port"

_AUTHORITY_STREAM_PREFIX = "factory.role_evidence_authority."
_AUTHORITY_SOURCE = "factory.pipeline"
_ABSENT_STATE = "absent_at_request_time"
_PRESENT_STATE = "present"
_LOCATOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@#?=&%+\-]{0,255}\Z")
_HASH_LENGTH = 64
_FRAGMENT_RAW_BYTES = 1024
_MAX_CUTOFF_BODY_BYTES = 64 * 1024
_MAX_CUTOFF_FRAGMENTS = 64
_MAX_SOURCE_ITEMS_PER_SLOT = 32
_MAX_SOURCE_ITEMS_TOTAL = 128
_FRAGMENT_ENCODING = "base64url"
_MAX_REQUEST_FREEZES_PER_GRANT = FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET
_STAGE_ROLE_AND_GRANT_CAP: dict[str, tuple[str, int]] = {
    "docs_generation": ("architect", 1),
    "pm_planning": ("pm", 2),
    "chief_engineer_review": ("chief_engineer", 1),
    "director_dispatch": ("director", 512),
    "quality_gate": ("qa", 1),
}
