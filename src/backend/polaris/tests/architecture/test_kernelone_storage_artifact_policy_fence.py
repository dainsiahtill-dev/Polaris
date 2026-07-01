"""Architecture fence for KernelOne storage policy ownership."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
STORAGE_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "storage"


def test_kernelone_storage_does_not_own_artifact_lifecycle_policy() -> None:
    """Polaris artifact lifecycle policy is owned by audit.verdict artifact_service."""
    retired_tokens = {
        "ARTIFACT_POLICY_METADATA",
        "get_artifact_policy_metadata",
        "should_archive_artifact",
        "should_compress_artifact",
        "DeprecationWarning",
        "warnings.warn",
    }

    offenders: list[str] = []
    for path in STORAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for token in retired_tokens:
            if token in source:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}::{token}")

    assert offenders == []
