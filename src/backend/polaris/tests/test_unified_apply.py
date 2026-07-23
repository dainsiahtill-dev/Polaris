from __future__ import annotations

from pathlib import Path

from polaris.cells.director.tasking.public import parse_all_operations


def test_tasking_protocol_is_parse_only_and_preserves_workspace(tmp_path: Path) -> None:
    target = tmp_path / "src" / "role_agent_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "def value() -> int:\n    return 1\n"
    target.write_text(original, encoding="utf-8")
    payload = (
        "PATCH_FILE: src/role_agent_service.py\n"
        "<<<<<<< SEARCH\n"
        "return 1\n"
        "=======\n"
        "return 2\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    parsed = parse_all_operations(payload)

    assert len(parsed) == 1
    assert target.read_text(encoding="utf-8") == original


def test_raw_full_file_and_delete_protocols_are_parse_only(tmp_path: Path) -> None:
    stale = tmp_path / "src" / "stale.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("x = 1\n", encoding="utf-8")
    payload = 'FILE: src/new_module.py\ndef ping() -> str:\n    return "ok"\nEND FILE\nDELETE_FILE: src/stale.py\n'

    parsed = parse_all_operations(payload)

    assert len(parsed) == 2
    assert not (tmp_path / "src" / "new_module.py").exists()
    assert stale.read_text(encoding="utf-8") == "x = 1\n"


def test_director_tasking_public_does_not_export_raw_apply_authority() -> None:
    import polaris.cells.director.tasking as tasking
    import polaris.cells.director.tasking.public as public

    for surface in (tasking, public):
        for name in ("apply_operation", "apply_all_operations", "apply_operations_strict"):
            assert not hasattr(surface, name)
