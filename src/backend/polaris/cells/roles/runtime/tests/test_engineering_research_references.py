import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.engineering_research import verify_references


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request(root: Path, entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    source = root / "manifest.json"
    raw = json.dumps({"runs/~": entries}).encode("utf-8")
    source.write_bytes(raw)
    return [
        {
            "manifest": source.name,
            "sha256": digest(raw),
            "references": [
                {"path_pointer": f"/runs~1~0/{i}/path", "digest_pointer": f"/runs~1~0/{i}/sha256"}
                for i in range(len(entries))
            ],
        }
    ]


def test_distinct_claims_for_one_file_keep_their_verdicts(tmp_path: Path) -> None:
    data = b"current evidence"
    (tmp_path / "result").write_bytes(data)
    query = request(
        tmp_path, [{"path": "result", "sha256": digest(data)}, {"path": "result", "sha256": digest(b"old")}]
    )
    result = verify_references(tmp_path, query)
    assert result["status"] == "changed"
    assert [r["status"] for r in result["reports"][0]["references"]] == ["matched", "changed"]
    assert result["distinct_targets_read"] == 1
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    result = verify_references(tmp_path, query)
    assert result["reports"][0]["status"] == "manifest_changed"
    assert result["counts"] == {"unproven": 2}


def test_missing_unavailable_and_invalid_pointers_are_not_matches(tmp_path: Path) -> None:
    data = b"safe"
    (tmp_path / "inside").write_bytes(data)
    (tmp_path / "alias").symlink_to(tmp_path / "inside")
    query = request(tmp_path, [{"path": name, "sha256": digest(data)} for name in ("absent", "alias", "../outside")])
    query[0]["references"].append({"path_pointer": "/runs~1~0/00/path", "digest_pointer": "/runs~1~0/0/sha256"})
    result = verify_references(tmp_path, query)
    assert result["status"] == "unproven" and result["counts"] == {"unproven": 4}
    assert result["reports"][0]["references"][-1]["reason"] == "invalid_pointer"


def test_empty_query_or_ambiguous_json_never_passes(tmp_path: Path) -> None:
    assert verify_references(tmp_path, [])["status"] == "unproven"
    query = request(tmp_path, [])
    assert verify_references(tmp_path, query)["status"] == "unproven"
    with pytest.raises(ValueError, match="references"):
        verify_references(tmp_path, [{"manifest": "x", "sha256": digest(b"x"), "references": "not a list"}])
    raw = b'{"p":"first","p":"second"}'
    (tmp_path / "manifest.json").write_bytes(raw)
    query[0].update(sha256=digest(raw), references=[{"path_pointer": "/p", "digest_pointer": "/p"}])
    result = verify_references(tmp_path, query)
    assert result["reports"][0]["status"] == "invalid_manifest"


def test_windows_root_mapping_is_explicit_and_component_scoped(tmp_path: Path) -> None:
    (tmp_path / "data").write_bytes(b"same")
    refs = request(
        tmp_path,
        [
            {"path": n, "sha256": digest(b"same")}
            for n in (
                "D:/old/research/data",
                "D:/old/research-extra/data",
                "D:/old/research/../research/data",
            )
        ],
    )
    assert verify_references(tmp_path, refs)["counts"] == {"unproven": 3}
    mapped = verify_references(tmp_path, refs, recorded_windows_root="D:/old/research")
    assert mapped["counts"] == {"matched": 1, "unproven": 2}
