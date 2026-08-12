"""Canonical JavaScript/Node repair rules for Director Runtime.

Package facade: re-exports domain modules for stable
``from ...repair_kernel.javascript_syntax import ...`` consumers.
"""

from __future__ import annotations

from .constants import (
    JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
    TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL,
)
from .dom_runtime import build_javascript_dom_global_runtime_guard_plan
from .esm_commonjs import build_javascript_esm_commonjs_entrypoint_plan
from .exports import (
    build_javascript_missing_export_plan,
    repair_javascript_export_contract_placeholders,
)
from .local_imports import build_typescript_local_js_import_plan
from .missing_methods import build_javascript_missing_method_runtime_plan
from .node_tests import (
    build_javascript_frontend_smoke_test_content,
    build_javascript_node_smoke_test_content,
    build_javascript_test_missing_target_plan,
    build_node_test_script_contract_plan,
    build_substantive_node_test_script,
)
from .npm_scripts import build_npm_script_contract_plan

__all__ = [
    "JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL",
    "JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL",
    "JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL",
    "JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL",
    "NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL",
    "NPM_SCRIPT_CONTRACT_SOURCE_TOOL",
    "TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL",
    "build_javascript_dom_global_runtime_guard_plan",
    "build_javascript_esm_commonjs_entrypoint_plan",
    "build_javascript_frontend_smoke_test_content",
    "build_javascript_missing_export_plan",
    "build_javascript_missing_method_runtime_plan",
    "build_javascript_node_smoke_test_content",
    "build_javascript_test_missing_target_plan",
    "build_node_test_script_contract_plan",
    "build_npm_script_contract_plan",
    "build_substantive_node_test_script",
    "build_typescript_local_js_import_plan",
]
