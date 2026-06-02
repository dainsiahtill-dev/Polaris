from polaris.infrastructure.realtime.process_local.message_event_fanout import RuntimeEventFanout


def test_file_edit_event_preserves_patch_unavailable_metadata() -> None:
    fanout = RuntimeEventFanout()

    event = fanout._build_file_edit_event(
        {
            "file_path": "src/app.ts",
            "operation": "modify",
            "content_size": 16,
            "timestamp": "2026-06-02T00:00:00+00:00",
            "diff_status": "unavailable",
            "patch_unavailable_reason": "no_content_change",
            "has_patch": False,
        },
    )

    assert event["diff_status"] == "unavailable"
    assert event["patch_unavailable_reason"] == "no_content_change"
    assert event["has_patch"] is False
    assert event["patch"] is None


def test_file_edit_event_preserves_available_patch_metadata() -> None:
    fanout = RuntimeEventFanout()

    event = fanout._build_file_edit_event(
        {
            "file_path": "src/app.ts",
            "operation": "modify",
            "content_size": 16,
            "timestamp": "2026-06-02T00:00:00+00:00",
            "patch": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
            "diff_status": "available",
            "has_patch": True,
        },
    )

    assert event["diff_status"] == "available"
    assert event["patch_unavailable_reason"] is None
    assert event["has_patch"] is True
    assert event["patch"].endswith("+new")
