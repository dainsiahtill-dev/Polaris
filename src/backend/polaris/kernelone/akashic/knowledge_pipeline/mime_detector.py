"""MIME Type Detection via Magic Bytes.

Provides accurate MIME type detection beyond file-extension guessing.
Uses file header signatures (magic bytes) for common document formats.

Architecture:
- Primary: magic bytes detection (accurate for known formats)
- Fallback: mimetypes.guess_type (extension-based)
- Override: explicit --mime-type CLI flag (highest priority)

Usage::

    detector = MagicMimeDetector()
    mime = detector.detect("/path/to/file.pdf", first_bytes=b"%PDF-1.4")
    # Returns "application/pdf"

    # Or with path only (reads first 512 bytes):
    mime = detector.detect_from_path("/path/to/file.docx")
"""

from __future__ import annotations

import logging
import mimetypes
import os

logger = logging.getLogger(__name__)

# Magic byte signatures: (magic_bytes, offset, mime_type)
# offset=-1 means match at any position from start
_MAGIC_SIGNATURES: list[tuple[bytes, int, str]] = [
    # PDF - must check before generic binary
    (b"%PDF-", 0, "application/pdf"),
    # Microsoft Office OLE Compound Document (legacy .doc, .xls, .ppt)
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "application/vnd.ms-office"),
    # Office Open XML (.docx, .xlsx, .pptx) — ZIP with specific entry
    (b"PK\x03\x04", 0, "application/zip"),
    # Rich Text Format
    (b"{\\rtf", 0, "application/rtf"),
    # HTML (may start with BOM or <html)
    (b"\xef\xbb\xbf<html", 0, "text/html"),
    (b"<html", 0, "text/html"),
    (b"<!DOCTYPE html", 0, "text/html"),
    (b"<!doctype html", 0, "text/html"),
    # Gzip compressed
    (b"\x1f\x8b", 0, "application/gzip"),
    # Bzip2
    (b"BZh", 0, "application/x-bzip2"),
    # PNG image
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    # JPEG image
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    # GIF
    (b"GIF87a", 0, "image/gif"),
    (b"GIF89a", 0, "image/gif"),
    # XML
    (b"<?xml", 0, "application/xml"),
    # UTF-8 BOM
    (b"\xef\xbb\xbf", 0, "text/plain"),
]


class MagicMimeDetector:
    """MIME type detector using magic bytes with extension-based fallback.

    Detection priority (highest to lowest):
    1. Explicit override (constructor parameter)
    2. Magic bytes detection
    3. mimetypes.guess_type (extension-based)
    4. application/octet-stream (final fallback)
    """

    def __init__(self, *, use_fallback: bool = True) -> None:
        self._use_fallback = use_fallback
        mimetypes.init()

    def detect(
        self,
        path: str | None,
        *,
        first_bytes: bytes | None = None,
        fallback_mime: str | None = None,
    ) -> str:
        """Detect MIME type from path and/or first bytes.

        Args:
            path: File path (used for extension fallback and reading first bytes)
            first_bytes: Pre-read first bytes of file (avoids extra read)
            fallback_mime: MIME type to return if detection fails

        Returns:
            Detected MIME type string.
        """
        # 1. Try magic bytes
        magic_mime = self._detect_magic(first_bytes)
        if magic_mime:
            # Refine ZIP detection to specific Office types using path
            if magic_mime == "application/zip" and path:
                refined = self._refine_zip_type(path)
                if refined:
                    return refined
            return magic_mime

        # 2. Extension-based fallback
        if path and self._use_fallback:
            guessed, _ = mimetypes.guess_type(path)
            if guessed:
                return guessed

        # 3. User-provided fallback
        if fallback_mime:
            return fallback_mime

        return "application/octet-stream"

    def detect_from_path(self, path: str, *, chunk_size: int = 512) -> str:
        """Detect MIME type by reading the file's first bytes.

        Args:
            path: Path to the file.
            chunk_size: Number of bytes to read for magic detection.

        Returns:
            Detected MIME type string.
        """
        try:
            with open(path, "rb") as f:
                first_bytes = f.read(chunk_size)
        except OSError:
            first_bytes = b""

        return self.detect(path, first_bytes=first_bytes)

    def _detect_magic(self, first_bytes: bytes | None) -> str | None:
        """Detect MIME type from magic bytes.

        Returns the MIME type of the first matching signature, or None.
        """
        if not first_bytes:
            return None

        for magic, offset, mime_type in _MAGIC_SIGNATURES:
            if offset >= 0:
                # Fixed offset match
                if len(first_bytes) > offset + len(magic) and first_bytes[offset : offset + len(magic)] == magic:
                    return mime_type
            # Anywhere-from-start match
            elif magic in first_bytes[:8192]:
                return mime_type

        return None

    def _refine_zip_type(self, path: str) -> str | None:
        """Refine generic ZIP MIME to specific Office XML types.

        Office Open XML files (.docx, .xlsx, .pptx) are ZIP archives
        containing [Content_Types].xml at the start.
        """
        name = os.path.basename(path).lower()

        # Check extension first (cheap)
        if name.endswith(".docx"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if name.endswith(".xlsx"):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if name.endswith(".pptx"):
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"

        # For unknown zip files, try to peek at the ZIP central directory
        # by checking if it's a valid OLE compound or Office Open XML
        try:
            import zipfile

            if zipfile.is_zipfile(path):
                try:
                    with zipfile.ZipFile(path, "r") as zf:
                        names = zf.namelist()[:5]
                        if "word/document.xml" in names:
                            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        if "xl/workbook.xml" in names or "xl/sharedStrings.xml" in names:
                            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        if "ppt/presentation.xml" in names:
                            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                except (RuntimeError, ValueError) as e:
                    logger.debug("ZIP content inspection failed for %s: %s", path, e)
        except (RuntimeError, ValueError) as e:
            logger.debug("ZIP validation failed for %s: %s", path, e)

        return None


# Singleton instance for reuse
_MIME_DETECTOR: MagicMimeDetector | None = None


def get_mime_detector() -> MagicMimeDetector:
    """Get the singleton MagicMimeDetector instance."""
    global _MIME_DETECTOR
    if _MIME_DETECTOR is None:
        _MIME_DETECTOR = MagicMimeDetector()
    return _MIME_DETECTOR


__all__ = ["MagicMimeDetector", "get_mime_detector"]
