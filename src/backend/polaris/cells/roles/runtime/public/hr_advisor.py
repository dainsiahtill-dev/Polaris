"""Optional HR RoleAgent host; configuration facts remain in LLM Control Plane.

The old lower HRAgent exports move here to keep normal configuration operations
independent of role hosting. This role is not a default delivery stage.
"""

from __future__ import annotations

from ..internal.hr_advisor import HRAgent

__all__ = ["HRAgent"]
