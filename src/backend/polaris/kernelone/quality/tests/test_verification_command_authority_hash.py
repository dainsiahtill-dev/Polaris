"""The shared hash must preserve the existing CE authority bytes."""

import pytest
from polaris.kernelone.quality import verification_commands


def test_verification_command_authority_hash_preserves_existing_ce_hash() -> None:
    assert (
        verification_commands.verification_command_authority_hash(
            task_id="TASK-VERIFY", modality="test", argv=("python", "verify.py"), cwd="."
        )
        == "90884e0931db9a2aad2793cef8f94e90d1057c272c196ea11a8c1e499286d197"
    )


@pytest.mark.parametrize(
    "task_id,modality,argv,expected",
    [
        (
            "TASK-A",
            "test",
            ("python", "-m", "pytest", "-q"),
            "c85709988047d0b899b2bbca89ba6be90a969ad77e252ef35dc1c6df0f7ae4f0",
        ),
        (
            "VERIFY",
            "test",
            ("python", "-m", "pytest", "-q"),
            "b603086af01ed1c48b560b1210acfd5d97bac3dfd18e434ca322be431a3c1886",
        ),
        (
            "TASK-A",
            "environment_prep",
            ("python", "-m", "pip", "install", "-e", "."),
            "0686d1475848e5ed50f348a1dd4384bad561d47ff0ba533e1768428a238472f2",
        ),
    ],
)
def test_original_authority_receipt_goldens(task_id: str, modality: str, argv: tuple[str, ...], expected: str) -> None:
    assert (
        verification_commands.verification_command_authority_hash(
            task_id=task_id, modality=modality, argv=argv, cwd="."
        )
        == expected
    )
