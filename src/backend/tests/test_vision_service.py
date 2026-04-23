"""Tests for polaris/cells/llm/control_plane/internal/vision_service.py.

Covers:
- Import safety without transformers/GPU (test_import_without_gpu)
- Lazy initialisation — no model load before factory call (test_lazy_init)
- trust_remote_code defaults to False (test_trust_remote_code_default_off)
- trust_remote_code enabled via env var, with warning log (test_trust_remote_code_env_enabled)
- unload_model emits a warning on CUDA cache flush failure (test_unload_model_warning_on_error)
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODULE_PATH = "polaris.cells.llm.control_plane.internal.vision_service"

# Resolve the absolute filesystem path once so we can load it directly,
# bypassing the package __init__.py chain (which in the current state of the
# repo has a pre-existing SyntaxError in an unrelated file deep in the chain).
_BACKEND_DIR = pathlib.Path(__file__).parent.parent.resolve()
_VISION_SERVICE_FILE = (
    _BACKEND_DIR
    / "polaris/cells/llm/control_plane/internal/vision_service.py"
)


def _reload_vision_module() -> types.ModuleType:
    """Force a clean re-import of vision_service, loaded directly from its file.

    We use ``spec_from_file_location`` instead of ``importlib.import_module``
    to avoid traversing the package ``__init__.py`` chain, which currently
    contains a pre-existing ``SyntaxError`` in an unrelated sibling module
    (``diff_tracker.py``).  The vision_service module itself has no
    intra-package imports, so loading it in isolation is both valid and
    intentional.
    """
    # Evict any cached copy
    if _MODULE_PATH in sys.modules:
        del sys.modules[_MODULE_PATH]

    spec = importlib.util.spec_from_file_location(_MODULE_PATH, _VISION_SERVICE_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_PATH] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# test_import_without_gpu
# ---------------------------------------------------------------------------


def test_import_without_gpu() -> None:
    """Importing vision_service must not raise even when torch/transformers are absent.

    Strategy: temporarily shadow both ``torch`` and ``transformers`` with
    stub modules that raise ``ImportError`` on import, then reload the target
    module and assert it loads cleanly with degraded flags.
    """
    # Build a fake sys.modules that makes torch/transformers un-importable
    _originals = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "torch" or k.startswith("transformers")}
    # Insert sentinel objects that will cause ImportError
    sys.modules["torch"] = None  # type: ignore[assignment]
    sys.modules["transformers"] = None  # type: ignore[assignment]

    try:
        module = _reload_vision_module()
        # Must load without exception
        assert module.ADVANCED_VISION_AVAILABLE is False
        assert module._TRANSFORMERS_AVAILABLE is False
        # VisionNotAvailableError must be importable from the module
        assert issubclass(module.VisionNotAvailableError, RuntimeError)
    finally:
        # Restore original sys.modules state
        del sys.modules["torch"]
        del sys.modules["transformers"]
        sys.modules.update(_originals)
        _reload_vision_module()


# ---------------------------------------------------------------------------
# test_lazy_init
# ---------------------------------------------------------------------------


def test_lazy_init() -> None:
    """get_vision_service() must not create an instance until it is first called."""
    # Reload to reset module-level singleton
    module = _reload_vision_module()

    # Before any call the internal singleton must be None
    assert module._service is None, "Module-level _service should be None before first call"

    # First call creates the instance
    svc = module.get_vision_service()
    assert svc is not None
    assert isinstance(svc, module.VisionService)

    # Subsequent calls return the same instance (singleton behaviour)
    svc2 = module.get_vision_service()
    assert svc is svc2


# ---------------------------------------------------------------------------
# test_trust_remote_code_default_off
# ---------------------------------------------------------------------------


def test_trust_remote_code_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_model must use trust_remote_code=False by default (no env var set)."""
    # Ensure env var is absent
    monkeypatch.delenv("KERNELONE_VISION_TRUST_REMOTE_CODE", raising=False)

    module = _reload_vision_module()

    # Patch ADVANCED_VISION_AVAILABLE and _TRANSFORMERS_AVAILABLE so the GPU
    # guard is bypassed and we reach the from_pretrained calls.
    mock_processor_cls = MagicMock()
    mock_model_cls = MagicMock()
    mock_model_instance = MagicMock()
    mock_model_cls.from_pretrained.return_value = mock_model_instance
    mock_model_instance.to.return_value = mock_model_instance

    with (
        patch.object(module, "ADVANCED_VISION_AVAILABLE", True),
        patch.object(module, "_TRANSFORMERS_AVAILABLE", True),
        patch.object(module, "AutoProcessor", mock_processor_cls),
        patch.object(module, "AutoModelForCausalLM", mock_model_cls),
    ):
        svc = module.VisionService()
        svc.load_model("some/model")

    # Both from_pretrained calls should have received trust_remote_code=False
    mock_processor_cls.from_pretrained.assert_called_once_with(
        "some/model", trust_remote_code=False
    )
    mock_model_cls.from_pretrained.assert_called_once_with(
        "some/model", trust_remote_code=False
    )


# ---------------------------------------------------------------------------
# test_trust_remote_code_env_enabled
# ---------------------------------------------------------------------------


def test_trust_remote_code_env_enabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When KERNELONE_VISION_TRUST_REMOTE_CODE=1, trust_remote_code=True is
    forwarded to from_pretrained AND a warning is emitted."""
    monkeypatch.setenv("KERNELONE_VISION_TRUST_REMOTE_CODE", "1")

    module = _reload_vision_module()

    mock_processor_cls = MagicMock()
    mock_model_cls = MagicMock()
    mock_model_instance = MagicMock()
    mock_model_cls.from_pretrained.return_value = mock_model_instance
    mock_model_instance.to.return_value = mock_model_instance

    with (
        patch.object(module, "ADVANCED_VISION_AVAILABLE", True),
        patch.object(module, "_TRANSFORMERS_AVAILABLE", True),
        patch.object(module, "AutoProcessor", mock_processor_cls),
        patch.object(module, "AutoModelForCausalLM", mock_model_cls),
        caplog.at_level(logging.WARNING, logger="app.services.vision_service"),
    ):
        svc = module.VisionService()
        svc.load_model("some/model")

    # from_pretrained must be called with trust_remote_code=True
    mock_processor_cls.from_pretrained.assert_called_once_with(
        "some/model", trust_remote_code=True
    )
    mock_model_cls.from_pretrained.assert_called_once_with(
        "some/model", trust_remote_code=True
    )

    # A warning log must have been emitted
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "KERNELONE_VISION_TRUST_REMOTE_CODE" in str(m) for m in warning_messages
    ), f"Expected warning about trust_remote_code env var, got: {warning_messages}"


# ---------------------------------------------------------------------------
# test_unload_model_warning_on_error
# ---------------------------------------------------------------------------


def test_unload_model_warning_on_error(caplog: pytest.LogCaptureFixture) -> None:
    """unload_model must emit logger.warning (not silently pass) when
    torch.cuda.empty_cache raises an exception."""
    module = _reload_vision_module()

    mock_torch = MagicMock()
    mock_torch.cuda.empty_cache.side_effect = RuntimeError("CUDA exploded")

    with (
        patch.object(module, "ADVANCED_VISION_AVAILABLE", True),
        patch.object(module, "_TRANSFORMERS_AVAILABLE", True),
        patch.object(module, "torch", mock_torch),
        caplog.at_level(logging.WARNING, logger="app.services.vision_service"),
    ):
        svc = module.VisionService()
        svc.unload_model()  # must not raise

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "CUDA cache" in str(m) or "empty_cache" in str(m) or "unload" in str(m).lower()
        for m in warning_messages
    ), f"Expected warning about CUDA cache flush failure, got: {warning_messages}"
