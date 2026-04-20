"""Extractor Registry for Knowledge Pipeline.

Provides centralized MIME type → ExtractorPort routing.
Folked from ToolHandlerRegistry pattern in polaris/kernelone/llm/toolkit/executor/handlers/registry.py.

Usage::

    registry = ExtractorRegistry()
    registry.register(TextExtractor())
    registry.register(MarkdownExtractor())

    extractor = registry.get("text/x-python")
    if extractor:
        fragments = await extractor.extract(doc)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import BaseExtractor

from polaris.kernelone.akashic.knowledge_pipeline.extractors.base import BaseExtractor
from polaris.kernelone.akashic.knowledge_pipeline.protocols import ExtractorPort

logger = logging.getLogger(__name__)


class ExtractorRegistry:
    """Registry mapping MIME types to ExtractorPort instances.

    Supports multiple extractors per MIME type (first match wins).

    Usage::

        registry = ExtractorRegistry()
        registry.register(TextExtractor())
        registry.register(MarkdownExtractor())

        # Get extractor for a specific MIME type
        extractor = registry.get("text/markdown")

        # Check if MIME type is supported
        if registry.supports("application/pdf"):
            ...

        # List all supported MIME types
        all_types = registry.supported_mime_types()
    """

    def __init__(self) -> None:
        self._extractors: list[BaseExtractor] = []
        self._mime_to_extractors: dict[str, list[BaseExtractor]] = {}

    def register(self, extractor: BaseExtractor) -> None:
        """Register an extractor.

        Args:
            extractor: An extractor instance implementing ExtractorPort.
        """
        if not isinstance(extractor, BaseExtractor) and not isinstance(extractor, ExtractorPort):
            raise TypeError(f"Extractor must be a BaseExtractor or ExtractorPort, got {type(extractor).__name__}")

        self._extractors.append(extractor)

        # Build MIME type index
        for mime_type in extractor.SUPPORTED_MIME_TYPES:
            if mime_type not in self._mime_to_extractors:
                self._mime_to_extractors[mime_type] = []
            self._mime_to_extractors[mime_type].append(extractor)

        logger.debug(
            "Registered extractor %s for MIME types: %s",
            type(extractor).__name__,
            extractor.SUPPORTED_MIME_TYPES,
        )

    def get(self, mime_type: str) -> BaseExtractor | None:
        """Get an extractor for a MIME type.

        Returns the first registered extractor that supports the MIME type,
        or None if no extractor is registered.

        Args:
            mime_type: The MIME type to find an extractor for.

        Returns:
            An extractor instance, or None.
        """
        extractors = self._mime_to_extractors.get(mime_type)
        if not extractors:
            return None
        return extractors[0]

    def get_all(self, mime_type: str) -> list[BaseExtractor]:
        """Get all extractors registered for a MIME type.

        Args:
            mime_type: The MIME type to find extractors for.

        Returns:
            List of all extractors supporting the MIME type (may be empty).
        """
        return list(self._mime_to_extractors.get(mime_type, []))

    def supports(self, mime_type: str) -> bool:
        """Check if any extractor supports the given MIME type.

        Args:
            mime_type: The MIME type to check.

        Returns:
            True if an extractor is registered for this MIME type.
        """
        return mime_type in self._mime_to_extractors

    def supported_mime_types(self) -> list[str]:
        """Get all MIME types with registered extractors.

        Returns:
            List of supported MIME type strings.
        """
        return list(self._mime_to_extractors.keys())

    def unregister(self, extractor: BaseExtractor) -> None:
        """Unregister an extractor.

        Args:
            extractor: The extractor instance to remove.
        """
        if extractor not in self._extractors:
            return

        self._extractors.remove(extractor)

        # Rebuild index for this extractor
        for mime_type in extractor.SUPPORTED_MIME_TYPES:
            if mime_type in self._mime_to_extractors:
                self._mime_to_extractors[mime_type] = [
                    e for e in self._mime_to_extractors[mime_type] if e is not extractor
                ]
                if not self._mime_to_extractors[mime_type]:
                    del self._mime_to_extractors[mime_type]

    def clear(self) -> None:
        """Remove all registered extractors."""
        self._extractors.clear()
        self._mime_to_extractors.clear()


# ---------------------------------------------------------------------------
# Default global registry with all built-in extractors
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY: ExtractorRegistry | None = None


def reset_default_registry() -> None:
    """Reset the global default registry (for testing isolation).

    After calling this, the next call to get_default_registry() will
    rebuild a fresh registry with all default extractors.
    """
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def get_default_registry() -> ExtractorRegistry:
    """Get the default global extractor registry.

    Lazily initializes with all built-in extractors on first call.

    Returns:
        The default ExtractorRegistry instance.
    """
    global _DEFAULT_REGISTRY

    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _build_default_registry()

    return _DEFAULT_REGISTRY


def _build_default_registry() -> ExtractorRegistry:
    """Build the default registry with all built-in extractors."""
    from polaris.kernelone.akashic.knowledge_pipeline.extractors import (
        CsvExtractor,
        DocxExtractor,
        HtmlExtractor,
        MarkdownExtractor,
        PDFExtractor,
        TextExtractor,
    )

    # Optional extractors (graceful degradation when dependency unavailable)
    _pptx_cls = None
    _xlsx_cls = None
    try:
        from polaris.kernelone.akashic.knowledge_pipeline.extractors import (
            PptxExtractor,
            XlsxExtractor,
        )

        _pptx_cls = PptxExtractor
        _xlsx_cls = XlsxExtractor
    except ImportError:
        pass

    registry = ExtractorRegistry()

    # Register in priority order (more specific document types first)
    registry.register(DocxExtractor())  # .docx
    if _xlsx_cls is not None:
        registry.register(_xlsx_cls())  # .xlsx
    if _pptx_cls is not None:
        registry.register(_pptx_cls())  # .pptx
    registry.register(PDFExtractor())  # .pdf
    registry.register(MarkdownExtractor())  # .md
    registry.register(CsvExtractor())  # .csv
    registry.register(HtmlExtractor())  # .html
    registry.register(TextExtractor())  # fallback for all text/* and other

    return registry


__all__ = ["ExtractorRegistry", "get_default_registry", "reset_default_registry"]
