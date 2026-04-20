"""`roles.host` cell - Unified host protocol core."""

from polaris.cells.roles.host.internal import UnifiedHostAdapter
from polaris.cells.roles.host.public import (
    HOST_KIND_PROFILES,
    AttachmentMode,
    HostCapabilityProfile,
    HostKind,
    RoleHostKind,
    SessionState,
    SessionType,
    get_capability_profile,
)

__all__ = [
    "HOST_KIND_PROFILES",
    "AttachmentMode",
    "HostCapabilityProfile",
    "HostKind",
    "RoleHostKind",
    "SessionState",
    "SessionType",
    "UnifiedHostAdapter",
    "get_capability_profile",
]
