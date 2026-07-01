"""Role-turn prompt appendix assembly.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

This module owns canonical request-level prompt appendix assembly so
``RoleExecutionKernel`` stays focused on turn coordination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest


def build_prompt_appendix_from_request(request: RoleTurnRequest) -> str:
    """Build the prompt appendix from canonical request fields.

    Duplicate non-empty appendix blocks are collapsed in first-seen order.
    """

    appendix_parts: list[str] = []
    seen: set[str] = set()

    if request.prompt_appendix:
        token = str(request.prompt_appendix).strip()
        if token and token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    extra_context = getattr(request, "extra_context", None)
    if extra_context:
        token = f"【额外上下文】\n{extra_context}"
        if token not in seen:
            seen.add(token)
            appendix_parts.append(token)

    return "\n\n".join(appendix_parts)
