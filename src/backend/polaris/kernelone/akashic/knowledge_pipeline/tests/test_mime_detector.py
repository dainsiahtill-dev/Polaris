"""Tests for MagicMimeDetector."""

from __future__ import annotations

import tempfile
from pathlib import Path

from polaris.kernelone.akashic.knowledge_pipeline.mime_detector import (
    MagicMimeDetector,
    get_mime_detector,
)


class TestMagicMimeDetector:
    """Tests for MagicMimeDetector."""

    def test_detect_pdf_by_magic_bytes(self) -> None:
        """PDF magic bytes are detected correctly."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/doc.pdf", first_bytes=b"%PDF-1.4 test content")
        assert mime == "application/pdf"

    def test_detect_pdf_by_path(self) -> None:
        """PDF detection works from path with actual file."""
        detector = MagicMimeDetector()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4\ntest content")
            f.flush()
            mime = detector.detect_from_path(f.name)
        assert mime == "application/pdf"
        Path(f.name).unlink()

    def test_detect_docx_by_zip_signature(self) -> None:
        """DOCX is detected as ZIP then refined to specific Office type."""
        detector = MagicMimeDetector()
        # Simulate ZIP with word document
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            # Write minimal ZIP with word/document.xml
            import zipfile

            with zipfile.ZipFile(f.name, "w") as zf:
                zf.writestr("word/document.xml", "<w:document/>")
            mime = detector.detect_from_path(f.name)
        assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        Path(f.name).unlink()

    def test_detect_xlsx_by_path(self) -> None:
        """XLSX is refined from generic ZIP to spreadsheet type."""
        detector = MagicMimeDetector()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            import zipfile

            with zipfile.ZipFile(f.name, "w") as zf:
                zf.writestr("xl/workbook.xml", "<workbook/>")
            mime = detector.detect_from_path(f.name)
        assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        Path(f.name).unlink()

    def test_detect_png_by_magic_bytes(self) -> None:
        """PNG magic bytes are detected correctly."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/image.png", first_bytes=b"\x89PNG\r\n\x1a\ndata")
        assert mime == "image/png"

    def test_detect_jpeg_by_magic_bytes(self) -> None:
        """JPEG magic bytes are detected correctly."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/image.jpg", first_bytes=b"\xff\xd8\xff\xe0test")
        assert mime == "image/jpeg"

    def test_detect_html_by_magic_bytes(self) -> None:
        """HTML is detected correctly."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/page.html", first_bytes=b"<html><body>test")
        assert mime == "text/html"

    def test_detect_octet_stream_for_unknown(self) -> None:
        """Unknown binary types fall back to application/octet-stream."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/file.xyz", first_bytes=b"\x00\x01\x02\x03garbage")
        assert mime == "application/octet-stream"

    def test_detect_with_no_bytes_returns_fallback(self) -> None:
        """Empty/missing bytes return fallback."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/file.txt", first_bytes=b"")
        assert mime == "text/plain"  # extension-based

    def test_detect_with_utf8_bom(self) -> None:
        """UTF-8 BOM is detected and returns text/plain."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/file.txt", first_bytes=b"\xef\xbb\xbfHello world")
        assert mime == "text/plain"

    def test_refine_zip_type_unknown_returns_none(self) -> None:
        """Unknown ZIP file (not Office) returns None from _refine_zip_type."""
        detector = MagicMimeDetector()
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            import zipfile

            with zipfile.ZipFile(f.name, "w") as zf:
                zf.writestr("data.json", "{}")
            mime = detector.detect_from_path(f.name)
        # Generic ZIP with no Office signature
        assert mime == "application/zip"
        Path(f.name).unlink()

    def test_singleton_instance(self) -> None:
        """get_mime_detector returns singleton."""
        d1 = get_mime_detector()
        d2 = get_mime_detector()
        assert d1 is d2

    def test_forced_mime(self) -> None:
        """forced_mime parameter is used when first_bytes is empty."""
        detector = MagicMimeDetector()
        mime = detector.detect("/tmp/random.xyz", first_bytes=b"", fallback_mime="application/custom")
        assert mime == "application/custom"
