from __future__ import annotations

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.extractors import docx_extractor
from polaris.kernelone.akashic.knowledge_pipeline.protocols import DocumentInput


@pytest.mark.asyncio
async def test_docx_extractor_falls_back_when_python_docx_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docx_extractor, "DOCX_AVAILABLE", False)

    extractor = docx_extractor.DocxExtractor()
    document = DocumentInput(
        source="sample.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content=b"fallback text\nsecond line",
    )

    fragments = await extractor.extract(document)

    assert extractor.is_available() is False
    assert [fragment.text for fragment in fragments] == ["fallback text\nsecond line"]
