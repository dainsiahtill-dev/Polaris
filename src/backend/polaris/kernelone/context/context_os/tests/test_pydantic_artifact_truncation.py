"""Regression coverage for frozen Pydantic artifact truncation."""

from polaris.kernelone.context.context_os.models_v2 import ArtifactRecordV2
from polaris.kernelone.context.context_os.runtime.ports import MAX_INLINE_CHARS
from polaris.kernelone.context.context_os.runtime.state import _ContextOSStateMixin


def test_large_pydantic_artifact_is_truncated_without_dataclass_replace() -> None:
    artifact = ArtifactRecordV2(
        artifact_id="artifact-large",
        content="x" * (MAX_INLINE_CHARS + 1),
        metadata=(("source", "test"),),
    )
    runtime = object.__new__(_ContextOSStateMixin)

    truncated = runtime._truncate_artifact_if_needed(artifact)

    assert truncated.artifact_id == artifact.artifact_id
    assert len(truncated.content) < len(artifact.content)
    assert dict(truncated.metadata)["truncated"] is True
    assert dict(truncated.metadata)["full_id"] == artifact.artifact_id

