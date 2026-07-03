# Artifact Quality Typed Diagnostics Blueprint (2026-07-04)

## 1. Problem

Artifact quality scanning already exposes `ArtifactQualityIssue`, but most
scanner findings still enter `ArtifactQualityEvidence` through
`errors: tuple[str, ...]` and are then reparsed into typed issues. That keeps
legacy string consumers working, but it makes each new diagnostic format a
potential parser drift point.

## 2. Target

`ArtifactQualityEvidence` remains string-compatible for legacy callers, while
new scanner code should prefer direct typed facts:

```
scanner/parser fact
  -> ArtifactQualityIssue(code, message, path, source, metadata)
  -> ArtifactQualityEvidence.issues
  -> string errors only as compatibility projection
```

Typed issues are evidence, not repair authorization. Repair still flows through
`Diagnostic -> Plan -> Policy/Execute -> Receipt -> Revalidate`.

## 3. 2026-07-04 Increment

- `_artifact_quality_evidence()` now accepts `issues=...` in addition to
  legacy `errors=...`.
- Direct issues are deduplicated against string-projected issues so callers do
  not receive one typed fact from the scanner and a second typed fact reparsed
  from the same legacy string.
- Workspace path scanner failures now emit direct typed issues:
  `workspace_path_unresolved` and `workspace_path_missing`.

## 4. Verification

- `rtk pytest src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py -q`
- `rtk ruff check src/backend/polaris/kernelone/quality/artifact_quality.py src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py`
- `rtk mypy src/backend/polaris/kernelone/quality/artifact_quality.py`
