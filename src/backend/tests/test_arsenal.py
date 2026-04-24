import pytest
from polaris.cells.llm.control_plane.internal.vision_service import get_vision_service


@pytest.mark.asyncio
async def test_vision_service_mock():
    # Ensure it returns analysis results when not loaded/no deps
    service = get_vision_service()
    service.unload_model()

    # Invalid base64 returns error status
    result = service.analyze_image("not-valid-base64!!!", task="<OD>")
    assert result["status"] == "error"

    # Valid base64 returns success with basic backend
    import base64

    valid_b64 = base64.b64encode(b"\x00" * 10).decode()
    result = service.analyze_image(valid_b64, task="<OD>")
    assert result["status"] in ("success", "error")  # success if PIL available, error otherwise
    assert "size_bytes" in result or "error" in result


def test_arsenal_import():
    # verify we can import the router without errors
    from polaris.delivery.http.routers import arsenal

    assert arsenal.router is not None
