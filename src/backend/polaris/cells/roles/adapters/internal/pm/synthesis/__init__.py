"""PM contract deterministic synthesis package.

This package is the lossless successor of the former ``synthesis`` module.
It re-exports every previously-public symbol from the same import path so
``import polaris.cells.roles.adapters.internal.pm.synthesis`` and
``from polaris.cells.roles.adapters.internal.pm.synthesis import X`` keep
resolving identically for all external importers.
"""

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing names that were module-level
# attributes of the former single-file module (preserves full dir() surface).
import re
from pathlib import Path
from typing import Any

from ..language_contracts import directive_requires_typescript_package_contract

# Ensure private helpers remain reachable as package attributes for any
# internal cross-module references that previously used module-level names.
from ._checks import (
    _CONTENT_ANY_RE,
    _DETERMINISTIC_CHECK_FULL_RE,
    _DETERMINISTIC_CHECK_RE,
    _DETERMINISTIC_CHECK_SECTION_TITLES,
    _DETERMINISTIC_CHECK_TOKEN_PATTERN,
    _MARKDOWN_ATX_HEADING_RE,
    _MARKDOWN_BLOCKQUOTE_RE,
    _MARKDOWN_FENCE_CLOSE_RE,
    _MARKDOWN_FENCE_OPEN_RE,
    _MARKDOWN_LIST_CONTAINER_RE,
    _MARKDOWN_LIST_ITEM_PREFIX_RE,
    _dedupe_limited_texts,
    _extract_content_any_keywords_from_directive,
    _extract_declared_deterministic_checks,
    _extract_deterministic_checks_from_directive,
    _markdown_atx_headings,
    _markdown_lines_outside_fences,
    _normalize_markdown_atx_heading_title,
)
from ._delivery import (
    _append_delivery_depth_to_contracts,
    _contract_keyword_tokens,
    _delivery_depth_contract,
    _delivery_plan_document,
    _domain_module_name,
    _extract_typescript_semantic_keywords,
    _javascript_model_target_from_keyword,
    _javascript_model_targets_from_keywords,
    _pascal_case_token,
    _pascal_identifier_token,
    _typescript_model_target_from_keyword,
    _typescript_model_targets_from_keywords,
    _with_delivery_depth_metadata,
)
from ._language import (
    _directive_has_other_explicit_primary_language,
    _directive_requires_cpp_package_contract,
    _directive_requires_go_workspace_contract,
    _directive_requires_java_package_contract,
    _directive_requires_javascript_package_contract,
    _directive_requires_language_root_delivery_contract,
    _directive_requires_python_workspace_contract,
    _directive_requires_rust_package_contract,
    _directive_requires_typescript_package_contract,
    _explicit_primary_language_from_directive,
)
from ._mixin import PMContractSynthesisMixin
from ._verification import (
    _attach_synthesized_verification_commands,
    _synthesized_contract_language,
    _synthesized_task_target_files,
    _synthesized_verifier_profile,
)
