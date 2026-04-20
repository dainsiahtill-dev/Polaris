"""Vision Service for screenshot and image analysis.

The module is import-safe in CPU/CI environments and only creates the singleton
service on first access. Advanced model loading is optional and disabled unless
transformers support and CUDA are available.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import os
from typing import Any

logger = logging.getLogger("app.services.vision_service")

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    Image = None
    PIL_AVAILABLE = False

torch: Any = None
AutoProcessor: Any = None
AutoModelForCausalLM: Any = None
_TRANSFORMERS_AVAILABLE = False
ADVANCED_VISION_AVAILABLE = False

try:
    import torch as _torch
    from transformers import AutoModelForCausalLM as _AutoModelForCausalLM, AutoProcessor as _AutoProcessor

    torch = _torch
    AutoProcessor = _AutoProcessor
    AutoModelForCausalLM = _AutoModelForCausalLM
    _TRANSFORMERS_AVAILABLE = True
    ADVANCED_VISION_AVAILABLE = bool(torch.cuda.is_available())
except ImportError:
    _TRANSFORMERS_AVAILABLE = False
    ADVANCED_VISION_AVAILABLE = False

VISION_AVAILABLE = PIL_AVAILABLE


class VisionNotAvailableError(RuntimeError):
    """Raised when advanced vision support is requested but unavailable."""


def _trust_remote_code_enabled() -> bool:
    raw = str(os.environ.get("POLARIS_VISION_TRUST_REMOTE_CODE") or "").strip().lower()
    enabled = raw in {"1", "true", "yes", "on"}
    if enabled:
        logger.warning("POLARIS_VISION_TRUST_REMOTE_CODE is enabled; remote model code execution is allowed.")
    return enabled


class VisionService:
    """Multi-backend vision analysis service."""

    def __init__(self) -> None:
        self.model: Any = None
        self.processor: Any = None
        self.is_loaded = False
        self._model_name = ""

    def load_model(self, model_name: str = "microsoft/Florence-2-large") -> bool:
        """Load an advanced vision model into GPU memory."""
        if not ADVANCED_VISION_AVAILABLE or not _TRANSFORMERS_AVAILABLE:
            logger.info("Advanced vision model not available (no GPU or transformers). Using basic mode.")
            return False

        if AutoProcessor is None or AutoModelForCausalLM is None:
            logger.info("Transformers classes are unavailable. Using basic mode.")
            return False

        trust_remote_code = _trust_remote_code_enabled()

        try:
            logger.info("Loading Vision Model: %s", model_name)
            self.processor = AutoProcessor.from_pretrained(
                model_name,
                trust_remote_code=trust_remote_code,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                trust_remote_code=trust_remote_code,
            ).to("cuda")
            self._model_name = model_name
            self.is_loaded = True
            logger.info("Vision Model loaded successfully.")
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.exception("Failed to load vision model.")
            self.is_loaded = False
            self.model = None
            self.processor = None
            self._model_name = ""
            return False

    def unload_model(self) -> None:
        """Unload model to free VRAM."""
        self.model = None
        self.processor = None
        self.is_loaded = False
        self._model_name = ""
        if ADVANCED_VISION_AVAILABLE and torch is not None:
            try:
                torch.cuda.empty_cache()
            except (AttributeError, OSError, RuntimeError):
                logger.warning("Failed to flush CUDA cache while unloading vision model.", exc_info=True)

    def get_status(self) -> dict[str, Any]:
        """Return current service status."""
        return {
            "pil_available": PIL_AVAILABLE,
            "advanced_available": ADVANCED_VISION_AVAILABLE,
            "transformers_available": _TRANSFORMERS_AVAILABLE,
            "model_loaded": self.is_loaded,
            "model_name": self._model_name,
            "capabilities": self._get_capabilities(),
        }

    def _get_capabilities(self) -> list[str]:
        caps: list[str] = []
        if PIL_AVAILABLE:
            caps.extend(["image_info", "format_detection", "resize"])
        if self.is_loaded:
            caps.extend(["object_detection", "captioning", "ocr"])
        return caps

    def analyze_image(self, image_base64: str, task: str = "<OD>") -> dict[str, Any]:
        """Analyze an image."""
        try:
            image_data = base64.b64decode(image_base64)
        except (binascii.Error, ValueError) as exc:
            return {"status": "error", "error": f"Invalid base64: {exc}"}

        if PIL_AVAILABLE and Image is not None:
            basic_info = self._analyze_basic(image_data)
        else:
            basic_info = {"size_bytes": len(image_data)}

        if self.is_loaded and PIL_AVAILABLE and Image is not None:
            try:
                advanced = self._analyze_advanced(image_data, task)
                return {**basic_info, **advanced, "status": "success", "backend": "advanced"}
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                VisionNotAvailableError,
            ):
                logger.warning("Advanced analysis failed, falling back to basic.", exc_info=True)

        return {**basic_info, "status": "success", "backend": "basic"}

    def _analyze_basic(self, image_data: bytes) -> dict[str, Any]:
        try:
            image = Image.open(io.BytesIO(image_data))
            return {
                "width": image.width,
                "height": image.height,
                "format": image.format or "unknown",
                "mode": image.mode,
                "size_bytes": len(image_data),
            }
        except (OSError, ValueError) as exc:
            return {"size_bytes": len(image_data), "error": str(exc)}

    def _analyze_advanced(self, image_data: bytes, task: str) -> dict[str, Any]:
        if Image is None or self.processor is None or self.model is None:
            raise VisionNotAvailableError("advanced vision model is not loaded")

        image = Image.open(io.BytesIO(image_data)).convert("RGB")
        inputs = self.processor(text=task, images=image, return_tensors="pt").to("cuda")

        generated_ids = self.model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            do_sample=False,
            num_beams=3,
        )
        text = self.processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        result = self.processor.post_process_generation(
            text,
            task=task,
            image_size=(image.width, image.height),
        )
        return {"analysis": result, "task": task}

    def extract_text(self, image_base64: str) -> dict[str, Any]:
        """Extract text from an image."""
        return self.analyze_image(image_base64, task="<OCR>")

    def describe_image(self, image_base64: str) -> dict[str, Any]:
        """Generate a caption/description for an image."""
        return self.analyze_image(image_base64, task="<CAPTION>")

    def detect_objects(self, image_base64: str) -> dict[str, Any]:
        """Detect objects in an image."""
        return self.analyze_image(image_base64, task="<OD>")


_service: VisionService | None = None


def get_vision_service() -> VisionService:
    """Get the singleton VisionService instance lazily."""
    global _service
    if _service is None:
        _service = VisionService()
    return _service


__all__ = [
    "ADVANCED_VISION_AVAILABLE",
    "PIL_AVAILABLE",
    "VISION_AVAILABLE",
    "_TRANSFORMERS_AVAILABLE",
    "VisionNotAvailableError",
    "VisionService",
    "get_vision_service",
]
