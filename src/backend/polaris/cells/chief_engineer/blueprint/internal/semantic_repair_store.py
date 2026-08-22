"""Owner-only persistence for schema-valid CE semantic repair candidates."""

from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.public.contracts._helpers import (
    _require_provenance_sha256,
    _require_safe_filename_token,
)
from polaris.cells.chief_engineer.blueprint.public.contracts._semantic_repair import (
    ChiefEngineerSemanticRepairCandidateV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


class ChiefEngineerSemanticRepairCandidateStore:
    """CAS-bound candidate store under the CE-owned runtime state root."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._fs = KernelFileSystem(workspace, get_default_adapter())

    def _path(self, *, project_id: str, run_id: str, candidate_hash: str) -> str:
        project_id = _require_safe_filename_token("project_id", project_id)
        run_id = _require_safe_filename_token("run_id", run_id)
        candidate_hash = _require_provenance_sha256("candidate_hash", candidate_hash)
        return f"runtime/state/blueprints/semantic-repair/{project_id}/{run_id}/{candidate_hash}.json"

    def persist(self, candidate: ChiefEngineerSemanticRepairCandidateV1) -> str:
        """Persist once; equal content is idempotent and divergent content fails."""

        if candidate.workspace != self._workspace:
            raise ValueError("candidate workspace does not match store workspace")
        path = self._path(
            project_id=candidate.project_id,
            run_id=candidate.run_id,
            candidate_hash=candidate.candidate_hash,
        )
        payload = candidate.to_dict()
        if self._fs.exists(path):
            existing = self._fs.read_json(path)
            if existing != payload:
                raise RuntimeError("semantic repair candidate CAS collision")
            return path
        self._fs.write_json_atomic(path, payload, indent=2, ensure_ascii=False)
        if self._fs.read_json(path) != payload:
            raise RuntimeError("semantic repair candidate atomic persistence verification failed")
        return path

    def load(
        self,
        *,
        project_id: str,
        run_id: str,
        candidate_hash: str,
    ) -> ChiefEngineerSemanticRepairCandidateV1 | None:
        """Load by exact run/project/hash CAS identity."""

        path = self._path(project_id=project_id, run_id=run_id, candidate_hash=candidate_hash)
        if not self._fs.exists(path):
            return None
        payload = self._fs.read_json(path)
        candidate = ChiefEngineerSemanticRepairCandidateV1.from_dict(payload)
        if (
            candidate.workspace != self._workspace
            or candidate.project_id != project_id
            or candidate.run_id != run_id
            or candidate.candidate_hash != candidate_hash
        ):
            raise ValueError("persisted semantic repair candidate CAS identity mismatch")
        return candidate


__all__ = ["ChiefEngineerSemanticRepairCandidateStore"]
