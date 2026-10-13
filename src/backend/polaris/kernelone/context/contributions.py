"""Trusted source contribution policy, independent of role/domain admission.

Model proposals remain raw audit observations. They cannot supply working facts
or prompt-visible artifact bodies until their owning workflow admits them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

MODEL_PROPOSAL = "model_proposal"


def is_model_proposal(role: str, metadata: Any) -> bool:
    if role != "assistant":
        return False
    values = metadata if isinstance(metadata, Mapping) else dict(metadata or ())
    return values.get("context_contribution") == MODEL_PROPOSAL


def model_proposal_metadata(content: str) -> dict[str, Any]:
    return {
        "context_contribution": MODEL_PROPOSAL,
        "proposal_sha256": sha256(content.encode("utf-8")).hexdigest(),
        "proposal_chars": len(content),
        "observation_only": True,
        "non_deliverable": True,
    }


def model_proposal_reference(content: str, metadata: Any = None) -> str:
    # Normalized ContextOS text may have different whitespace from the raw
    # observation. Preserve the digest produced before normalization.
    source = metadata if isinstance(metadata, Mapping) else dict(metadata or ())
    digest = source.get("proposal_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        digest = sha256(content.encode("utf-8")).hexdigest()
    chars = source.get("proposal_chars")
    if type(chars) is not int or not 0 <= chars <= 2**63 - 1:
        chars = len(content)
    return f"[model_proposal status=unadmitted chars={chars} sha256={digest}]"


def prompt_contribution_content(role: str, content: str, metadata: Any) -> str:
    """Shape only prompt projection; never rewrite raw source/audit content."""
    return model_proposal_reference(content, metadata) if is_model_proposal(role, metadata) else content
