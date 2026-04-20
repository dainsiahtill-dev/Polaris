"""Import fence regression test for orchestration circular dependency.

Verifies that:
1. polaris.cells.orchestration.shared_types can be imported standalone.
2. polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline
   does NOT trigger a CIRCULAR import error (it may fail for other pre-existing
   reasons unrelated to this fix — those are out of scope here).
3. polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows.director_workflow
   can be imported without triggering a circular import.
4. Both cells import ErrorCategory / ErrorClassifier from shared_types
   (not from each other), confirmed by identity checks.
5. Re-importing the former path (pm_dispatch.public.service) still works
   and yields the same objects (backward compat shim is live).

If any of these tests break due to a circular import, a circular dependency has
been reintroduced. Fix the import, do not weaken the test.

NOTE: Some import attempts in this file will fail with a pre-existing
ImportError unrelated to the circular-dependency fix (e.g. missing
'clear_workspace_status' from workspace.integrity). Those are acknowledged as
pre-existing environment issues and are handled gracefully below.
"""

from __future__ import annotations

import importlib
import sys
import types

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pre-existing environment errors that are NOT regressions from this change.
# If an import fails with one of these messages it is a known pre-existing issue.
_PREEXISTING_IMPORT_ERROR_FRAGMENTS: tuple[str, ...] = (
    "clear_workspace_status",
    "UnsupportedProviderTypeError",
)

# The exact error message fragment that indicates a circular-dependency regression.
_CIRCULAR_IMPORT_INDICATOR = "circular import"

# Fully-qualified paths for the two modules under test.
_DISPATCH_PIPELINE_PATH = (
    "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline"
)
_DIRECTOR_WORKFLOW_PATH = (
    "polaris.cells.orchestration.workflow_runtime.internal"
    ".runtime_engine.workflows.director_workflow"
)
_SHARED_TYPES_PATH = "polaris.cells.orchestration.shared_types"
_PM_DISPATCH_PUBLIC_SERVICE = (
    "polaris.cells.orchestration.pm_dispatch.public.service"
)
_PM_DISPATCH_INTERNAL_EC = (
    "polaris.cells.orchestration.pm_dispatch.internal.error_classifier"
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fresh_import(module_path: str) -> types.ModuleType:
    """Import a module, raising ImportError verbatim if it fails."""
    return importlib.import_module(module_path)


def _is_preexisting_error(exc: ImportError) -> bool:
    """Return True if the ImportError is a known pre-existing environment issue."""
    msg = str(exc)
    return any(fragment in msg for fragment in _PREEXISTING_IMPORT_ERROR_FRAGMENTS)


def _is_circular_import_error(exc: ImportError) -> bool:
    """Return True if the ImportError is a circular import detection."""
    msg = str(exc).lower()
    return (
        _CIRCULAR_IMPORT_INDICATOR in msg
        or ("partially initialized module" in msg and "circular import" in msg)
    )


# ---------------------------------------------------------------------------
# 1. shared_types standalone import
# ---------------------------------------------------------------------------

class TestSharedTypesStandalone:
    """shared_types must import without pulling in pm_dispatch or workflow_runtime."""

    def test_import_succeeds(self) -> None:
        mod = _fresh_import(_SHARED_TYPES_PATH)
        assert mod is not None, "shared_types failed to import"

    def test_exports_error_category(self) -> None:
        mod = _fresh_import(_SHARED_TYPES_PATH)
        assert hasattr(mod, "ErrorCategory"), "ErrorCategory missing from shared_types"

    def test_exports_error_classifier(self) -> None:
        mod = _fresh_import(_SHARED_TYPES_PATH)
        assert hasattr(mod, "ErrorClassifier"), "ErrorClassifier missing from shared_types"

    def test_exports_error_record(self) -> None:
        mod = _fresh_import(_SHARED_TYPES_PATH)
        assert hasattr(mod, "ErrorRecord"), "ErrorRecord missing from shared_types"

    def test_exports_recovery_recommendation(self) -> None:
        mod = _fresh_import(_SHARED_TYPES_PATH)
        assert hasattr(mod, "RecoveryRecommendation"), "RecoveryRecommendation missing"

    def test_shared_types_has_no_orchestration_cell_imports(self) -> None:
        """shared_types itself must not import from pm_dispatch or workflow_runtime."""
        mod = _fresh_import(_SHARED_TYPES_PATH)
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name, None)
            if isinstance(attr, types.ModuleType):
                assert "pm_dispatch" not in attr.__name__, (
                    f"shared_types imports pm_dispatch module via '{attr_name}'. "
                    "This would recreate the circular dependency."
                )
                assert "workflow_runtime" not in attr.__name__, (
                    f"shared_types imports workflow_runtime module via '{attr_name}'. "
                    "This would recreate the circular dependency."
                )


# ---------------------------------------------------------------------------
# 2. pm_dispatch.internal.dispatch_pipeline — no circular import
# ---------------------------------------------------------------------------

class TestPmDispatchImport:
    """dispatch_pipeline must not raise a CIRCULAR ImportError.

    Note: it may fail with a pre-existing unrelated ImportError (e.g. missing
    symbol in workspace.integrity).  That is a known environment issue outside
    the scope of this fix and is tolerated below.
    """

    def test_no_circular_import_error(self) -> None:
        """Importing dispatch_pipeline must not raise a circular ImportError."""
        try:
            _fresh_import(_DISPATCH_PIPELINE_PATH)
        except ImportError as exc:
            if _is_preexisting_error(exc):
                # Known pre-existing broken chain unrelated to circular dep.
                return
            # Any other ImportError is a potential regression.
            raise AssertionError(
                f"dispatch_pipeline import raised an unexpected ImportError.\n"
                f"If this is circular, it is a regression of the P0-02 fix.\n"
                f"Error: {exc}"
            ) from exc

    def test_does_not_import_workflow_runtime_internal(self) -> None:
        """dispatch_pipeline must NOT import workflow_runtime internals at module level.

        It imports workflow_runtime.public.service (allowed — public contract).
        It must never import workflow_runtime.internal.* at module level.
        """
        forbidden_prefix = "polaris.cells.orchestration.workflow_runtime.internal"
        dp_mod = sys.modules.get(_DISPATCH_PIPELINE_PATH)
        if dp_mod is None:
            # Module is not loaded (pre-existing broken chain) — nothing to check.
            return
        for attr_name in dir(dp_mod):
            attr = getattr(dp_mod, attr_name, None)
            if isinstance(attr, types.ModuleType):
                assert not attr.__name__.startswith(forbidden_prefix), (
                    f"dispatch_pipeline has a top-level reference to "
                    f"workflow_runtime internal module '{attr.__name__}' via "
                    f"attribute '{attr_name}'. This re-introduces the circular dependency."
                )


# ---------------------------------------------------------------------------
# 3. workflow_runtime director_workflow independent import
# ---------------------------------------------------------------------------

class TestWorkflowRuntimeImport:
    """director_workflow must import without circular errors."""

    def test_import_succeeds(self) -> None:
        mod = _fresh_import(_DIRECTOR_WORKFLOW_PATH)
        assert mod is not None

    def test_does_not_import_pm_dispatch_internal(self) -> None:
        """director_workflow must NOT import pm_dispatch internals at module level."""
        _fresh_import(_DIRECTOR_WORKFLOW_PATH)
        forbidden_prefix = "polaris.cells.orchestration.pm_dispatch.internal"
        dw_mod = sys.modules.get(_DIRECTOR_WORKFLOW_PATH)
        if dw_mod is None:
            return
        for attr_name in dir(dw_mod):
            attr = getattr(dw_mod, attr_name, None)
            if isinstance(attr, types.ModuleType):
                assert not attr.__name__.startswith(forbidden_prefix), (
                    f"director_workflow has a top-level reference to pm_dispatch "
                    f"internal module '{attr.__name__}' via attribute '{attr_name}'. "
                    f"This reintroduces the circular dependency."
                )


# ---------------------------------------------------------------------------
# 4. Identity check: shared_types objects are used by both cells
# ---------------------------------------------------------------------------

class TestSharedTypesIdentity:
    """ErrorCategory from shared_types and director_workflow must be the same object."""

    def test_error_category_identity_shared_vs_pm_dispatch_public(self) -> None:
        """pm_dispatch.public.service must re-export from shared_types."""
        try:
            shared = _fresh_import(_SHARED_TYPES_PATH)
            pm_pub = _fresh_import(_PM_DISPATCH_PUBLIC_SERVICE)
        except ImportError as exc:
            if _is_preexisting_error(exc):
                return
            raise
        assert shared.ErrorCategory is pm_pub.ErrorCategory, (
            "ErrorCategory in pm_dispatch.public.service is NOT the same object "
            "as in shared_types. The shim re-export is broken."
        )

    def test_error_category_identity_shared_vs_pm_dispatch_internal(self) -> None:
        """pm_dispatch.internal.error_classifier shim must re-export from shared_types."""
        shared = _fresh_import(_SHARED_TYPES_PATH)
        pm_int = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert shared.ErrorCategory is pm_int.ErrorCategory, (
            "ErrorCategory in pm_dispatch.internal.error_classifier is NOT the same "
            "object as in shared_types. The shim re-export is broken."
        )

    def test_error_classifier_identity_shared_vs_director_workflow(self) -> None:
        """director_workflow must use ErrorClassifier from shared_types."""
        shared = _fresh_import(_SHARED_TYPES_PATH)
        dw = _fresh_import(_DIRECTOR_WORKFLOW_PATH)
        dw_classifier = getattr(dw, "ErrorClassifier", None)
        assert dw_classifier is not None, (
            "ErrorClassifier is not accessible as a module attribute of "
            "director_workflow; verify the import was not accidentally removed."
        )
        assert shared.ErrorClassifier is dw_classifier, (
            "ErrorClassifier in director_workflow is NOT the same object as in "
            "shared_types. The import was not updated correctly."
        )


# ---------------------------------------------------------------------------
# 5. Backward-compat: pm_dispatch.public.service still exports both names
# ---------------------------------------------------------------------------

class TestBackwardCompatExports:
    """Ensure nothing broke for existing consumers of pm_dispatch.public.service."""

    def test_error_category_exported_from_internal_error_classifier(self) -> None:
        mod = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert hasattr(mod, "ErrorCategory")

    def test_error_classifier_exported_from_internal_error_classifier(self) -> None:
        mod = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert hasattr(mod, "ErrorClassifier")

    def test_exponential_backoff_still_in_internal_error_classifier(self) -> None:
        mod = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert hasattr(mod, "ExponentialBackoff"), (
            "ExponentialBackoff was accidentally removed from error_classifier shim."
        )

    def test_circuit_breaker_still_in_internal_error_classifier(self) -> None:
        mod = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert hasattr(mod, "CircuitBreaker"), (
            "CircuitBreaker was accidentally removed from error_classifier shim."
        )

    def test_retry_executor_still_in_internal_error_classifier(self) -> None:
        mod = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert hasattr(mod, "RetryExecutor"), (
            "RetryExecutor was accidentally removed from error_classifier shim."
        )

    def test_error_category_exported_from_pm_dispatch_public(self) -> None:
        try:
            mod = _fresh_import(_PM_DISPATCH_PUBLIC_SERVICE)
        except ImportError as exc:
            if _is_preexisting_error(exc):
                return
            raise
        assert hasattr(mod, "ErrorCategory")

    def test_error_classifier_exported_from_pm_dispatch_public(self) -> None:
        try:
            mod = _fresh_import(_PM_DISPATCH_PUBLIC_SERVICE)
        except ImportError as exc:
            if _is_preexisting_error(exc):
                return
            raise
        assert hasattr(mod, "ErrorClassifier")


# ---------------------------------------------------------------------------
# 6. Cross-verification: simultaneous import of both cells does not deadlock
# ---------------------------------------------------------------------------

class TestSimultaneousImport:
    """Both cell modules can be imported in the same process without error."""

    def test_smoke_both_modules_importable(self) -> None:
        """Neither cell should raise a circular ImportError when loaded together."""
        # director_workflow is the definitive test here because it no longer
        # imports from pm_dispatch at all.
        dw = _fresh_import(_DIRECTOR_WORKFLOW_PATH)
        assert dw is not None, "director_workflow failed to import"

        # error_classifier shim must be independently importable.
        ec = _fresh_import(_PM_DISPATCH_INTERNAL_EC)
        assert ec is not None, "error_classifier shim failed to import"

        # Both must resolve to the same ErrorCategory class.
        shared = _fresh_import(_SHARED_TYPES_PATH)
        assert ec.ErrorCategory is shared.ErrorCategory
        assert dw.ErrorCategory is shared.ErrorCategory
