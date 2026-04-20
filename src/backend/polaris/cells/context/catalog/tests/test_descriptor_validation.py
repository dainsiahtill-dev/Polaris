from polaris.cells.context.catalog import validate_descriptor_cache_payload


def test_validate_descriptor_cache_payload_reports_missing_fields() -> None:
    errors = validate_descriptor_cache_payload(
        {
            "version": 1,
            "generated_at": "2026-03-19T00:00:00Z",
            "workspace": "x",
            "embedding_runtime_fingerprint": "seed",
            "descriptors": [{}],
        }
    )

    assert errors
    assert any("missing field: cell_id" in error for error in errors)
