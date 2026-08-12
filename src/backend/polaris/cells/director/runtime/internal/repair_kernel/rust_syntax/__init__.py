"""Rust deterministic repair planners owned by Director Runtime.

This package is the lossless successor of the former ``rust_syntax`` module.
It re-exports every previously-public symbol from the same import path so
``import ...repair_kernel.rust_syntax`` and ``from ...rust_syntax import X``
keep resolving identically. Import-time bindings that previously lived on the
module (stdlib re-exports, contract types, source-tool constants) are preserved
here for exact ``dir()`` surface parity.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing names that
# were module-level attributes of the former ``rust_syntax`` module.
import re
from collections.abc import Mapping, Sequence

import tomllib

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._constants import (
    RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
    RUST_CRATE_IMPORT_SOURCE_TOOL,
    RUST_DEPENDENCY_SOURCE_TOOL,
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
    RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
    RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
    RUST_LINE_SUGGESTION_SOURCE_TOOL,
    RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
    RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_STUB,
    RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
    RUST_POST_SOURCE_TOOL,
    RUST_SERDE_DERIVE_SOURCE_TOOL,
    RUST_TRAIT_IMPORT_SOURCE_TOOL,
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
    RUST_UNUSED_IMPORT_SOURCE_TOOL,
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
)
from ._plans import (
    build_rust_crate_import_plan,
    build_rust_crate_import_rewrite_plan,
    build_rust_dependency_plan,
    build_rust_duplicate_module_file_plan,
    build_rust_field_rename_suggestion_plan,
    build_rust_incompatible_copy_derive_plan,
    build_rust_line_suggestion_plan,
    build_rust_method_self_signature_plan,
    build_rust_missing_binary_entrypoint_plan,
    build_rust_missing_module_file_plan,
    build_rust_missing_trait_derive_plan,
    build_rust_serde_derive_plan,
    build_rust_trait_import_plan,
    build_rust_unresolved_pub_use_plan,
    build_rust_unused_import_plan,
    build_rust_wrong_crate_path_plan,
)
