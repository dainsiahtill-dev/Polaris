from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.write_authority import (
    extract_target_path_from_payload,
    is_authoritative_write_result,
)


def test_extracts_path_from_nested_edit_blocks_results() -> None:
    payload = {
        "result": {
            "blocks_applied": 1,
            "results": [
                {
                    "file": "src/client/three-scene.ts",
                    "bytes_changed": 120,
                }
            ],
        }
    }

    assert extract_target_path_from_payload(payload) == "src/client/three-scene.ts"


def test_extracts_path_from_nested_effect_receipt_file_list() -> None:
    payload = {
        "result": {
            "effect_receipt": {
                "files_modified": ["src/server/app.ts"],
                "operation": "modify",
            }
        }
    }

    assert extract_target_path_from_payload(payload) == "src/server/app.ts"


def test_extracts_path_from_nested_result_payload() -> None:
    payload = {
        "result": {
            "success": True,
            "payload": {
                "file": "src/game/card-catalog.ts",
                "effect_receipt": {"file": "src/game/card-catalog.ts"},
            },
        }
    }

    assert extract_target_path_from_payload(payload) == "src/game/card-catalog.ts"


def test_authoritative_write_accepts_nested_edit_blocks_receipt() -> None:
    result = {
        "tool": "edit_blocks",
        "success": True,
        "result": {
            "blocks_applied": 1,
            "effect_receipt": {
                "files_modified": ["src/client/three-scene.ts"],
                "operation": "modify",
            },
        },
    }

    assert is_authoritative_write_result(result) is True


def test_authoritative_write_accepts_nested_result_payload_receipt() -> None:
    result = {
        "tool": "write_file",
        "result": {
            "success": True,
            "payload": {
                "file": "src/game/card-catalog.ts",
                "effect_receipt": {"file": "src/game/card-catalog.ts"},
            },
        },
    }

    assert is_authoritative_write_result(result) is True
