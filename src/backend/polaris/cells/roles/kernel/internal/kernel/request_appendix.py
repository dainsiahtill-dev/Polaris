"""Role-turn prompt appendix assembly.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

This module owns request-level prompt appendix compatibility so
``RoleExecutionKernel`` does not expose a separate helper method for deprecated
request fields.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest

_DEPRECATED_SYSTEM_PROMPT_WARNING = "RoleTurnRequest.system_prompt is deprecated; use prompt_appendix instead."


def build_prompt_appendix_from_request(request: RoleTurnRequest) -> str:
    """Build the prompt appendix from canonical and deprecated request fields.

    ``prompt_appendix`` is the canonical field. ``system_prompt`` is still read
    as a compatibility input so historical callers receive the same prompt
    material and a deprecation warning. Duplicate non-empty appendix blocks are
    collapsed in first-seen order.
    """

    appendix_parts: list[str] = []
    seen: set[str] = set()

    if request.prompt_appendix:
        token = str(request.prompt_appendix).strip()
        if token and token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    if request.system_prompt:
        token = str(request.system_prompt).strip()
        if token:
            warnings.warn(
                _DEPRECATED_SYSTEM_PROMPT_WARNING,
                DeprecationWarning,
                stacklevel=2,
            )
            if token not in seen:
                seen.add(token)
                appendix_parts.append(token)

    extra_context = getattr(request, "extra_context", None)
    if extra_context:
        token = f"【额外上下文】\n{extra_context}"
        if token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    return "\n\n".join(appendix_parts)
