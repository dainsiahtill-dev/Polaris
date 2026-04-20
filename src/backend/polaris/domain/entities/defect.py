"""Domain entity constants for defect tracking.

This module provides the single source of truth for defect-related constants.
All modules should import from here instead of defining their own versions.
"""

# Canonical definition of required fields for defect tickets
DEFAULT_DEFECT_TICKET_FIELDS: list[str] = [
    "defect_id",
    "severity",
    "repro_steps",
    "expected",
    "actual",
    "artifact_path",
    "suspected_scope",
]


__all__ = [
    "DEFAULT_DEFECT_TICKET_FIELDS",
]
