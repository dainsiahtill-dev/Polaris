from polaris.cells.context.engine.internal.search_gateway import get_search_service
from polaris.cells.qa.audit_verdict.internal.quality_service import get_quality_service
from polaris.cells.runtime.artifact_store.internal.arrow_service import get_arrow_service
from polaris.cells.workspace.integrity.public.service import get_code_parser


def test_arrow_fallback():
    service = get_arrow_service()
    data = [{"id": 1, "name": "test"}]

    if service.available:
        result = service.to_arrow_ipc(data)
        assert result is not None
        assert isinstance(result, bytes)
        assert len(result) > 0
    else:
        result = service.to_arrow_ipc(data)
        assert result is None


def test_code_parser_fallback():
    parser = get_code_parser()

    # Python test
    code = "def hello(): pass"
    result = parser.parse_file(code, ".py")

    if parser.available:
        assert result["parsed"] is True
        assert result["language"] == "python"
        assert result["node_type"] == "module"
    else:
        assert result["parsed"] is False
        assert result["reason"] == "unsupported_or_missing_lib"


def test_parser_unsupported_ext():
    parser = get_code_parser()
    result = parser.parse_file("some txt", ".txt")
    assert result["parsed"] is False


def test_quality_service():
    svc = get_quality_service()
    # Should work conditionally based on if user has ruff installed
    # We just check the API contract
    res = svc.lint_code("def foo(): pas", ".py")
    if svc.available:
        # It might fail syntax error or lint error
        assert "success" in res
    else:
        assert res["success"] is False


def test_search_service():
    """Catalog-backed search: graph-derived descriptors, not Tantivy."""
    svc = get_search_service()
    results = svc.search("catalog")
    assert isinstance(results, list)
    for item in results:
        assert "cell_id" in item
        assert "path" in item
