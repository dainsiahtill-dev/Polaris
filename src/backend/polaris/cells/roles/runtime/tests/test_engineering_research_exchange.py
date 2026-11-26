"""A producer publication is immutable and a consumer makes evidence visible only after all bytes verify."""

import hashlib
import json
import time
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_research_exchange import (
    adopt_research_publications,
    install_publication,
    publication_hash,
    read_publication_blob,
    seal_verified_files,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_research import project_lab
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_qa import _calls, _scope, _text


def test_sealed_versions_are_independent_of_live_source_and_share_bytes(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    receiver = tmp_path / "receiver"
    producer.mkdir()
    receiver.mkdir()
    control = producer / ".polaris/runtime/engineering"
    control.mkdir(parents=True)
    data = b"original measurement\x00\xff"
    digest = hashlib.sha256(data).hexdigest()
    for name in ("first.bin", "second.bin"):
        (producer / name).write_bytes(data)
    verified = {str(producer / name): {"sha256": digest} for name in ("first.bin", "second.bin")}
    files = seal_verified_files(producer, control, ["second.bin", "first.bin"], verified, lambda path: True)
    desc = {
        "schema": "engineering.research-publication.v1",
        "producer_workspace": str(producer),
        "run_id": "measured",
        "project": "study",
        "lab": "source",
        "experiment": "sample",
        "scope": "finite sample",
        "plan_sha256": "a" * 64,
        "verification_seq": 7,
        "workspace_revision": 2,
        "files": files,
    }
    pub = publication_hash(desc)
    (producer / "first.bin").write_bytes(b"updated")
    assert read_publication_blob(control, files[0]) == data
    with pytest.raises(ValueError, match="changed"):
        seal_verified_files(producer, control, ["first.bin"], verified, lambda path: True)
    calls: list[int] = []

    def fetch(index: int) -> bytes:
        calls.append(index)
        return read_publication_blob(control, files[index])

    def allow(_path: str) -> bool:
        return True

    first = install_publication(receiver, pub, desc, fetch, allow)
    assert len(calls) == 1
    again = install_publication(receiver, pub, desc, fetch, allow)
    assert again == first and len(calls) == 1
    manifest = (receiver / first["report"]["manifest"]).read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == first["report"]["sha256"]
    rows = json.loads(manifest)["artifacts"]
    assert [r["source_path"] for r in rows] == ["first.bin", "second.bin"]
    assert (receiver / rows[0]["path"]).read_bytes() == data
    base = {
        "schema": "engineering.research-plan.v1",
        "project": "study",
        "experiments": [
            {"lab": "source", "id": "sample", "scope": "original"},
            {
                "lab": "integration",
                "id": "output",
                "scope": "consumer",
                "report": first["report"],
                "depends_on": [{"lab": "source", "id": "sample"}],
            },
        ],
    }
    plan = adopt_research_publications(receiver, base, [pub], ["integration/output"])
    assert "report" not in plan["experiments"][1] and "report" in base["experiments"][1]
    assert plan["experiments"][1]["depends_on"][0]["sha256"] == first["report"]["sha256"]
    assert project_lab(receiver, plan, "integration")["state_counts"] == {"matched": 1, "needs_recheck": 1}
    false_claim = {
        "schema": base["schema"],
        "project": base["project"],
        "experiments": [{"lab": "integration", "id": "other", "scope": desc["scope"], "report": first["report"]}],
    }
    assert project_lab(receiver, false_claim, "integration")["state_counts"] == {"unproven": 1}
    # Binding a new input in the plan cannot change the producer's recorded input.
    consumer = {
        **desc,
        "lab": "integration",
        "experiment": "output",
        "scope": "consumer",
        "depends_on": [{"lab": "source", "id": "sample", "sha256": "d" * 64}],
    }
    historical = install_publication(receiver, publication_hash(consumer), consumer, fetch, allow)
    plan["experiments"][1].update({"report": historical["report"]})
    status = project_lab(receiver, plan, "integration")["experiments"][-1]
    assert status["state"] == "needs_recheck" and status["affected_by"] == ["source/sample"]
    (receiver / rows[0]["path"]).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="integrity"):
        install_publication(receiver, pub, desc, fetch, allow)
    assert (receiver / rows[0]["path"]).read_bytes() == b"damaged"


def test_unverified_missing_or_denied_data_cannot_commit_a_publication(tmp_path: Path) -> None:
    control = tmp_path / "controls"
    control.mkdir()
    (tmp_path / "sample").write_bytes(b"x")
    with pytest.raises(ValueError, match="verified"):
        seal_verified_files(tmp_path, control, ["sample"], {}, lambda name: True)
    with pytest.raises(PermissionError):
        seal_verified_files(tmp_path, control, ["sample"], {}, lambda name: False)


def test_interrupted_transfer_commits_only_after_retry_and_keeps_lost_intent_unknown(tmp_path: Path) -> None:
    contents = [b"calibration", b"measurement"]
    files = [
        {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for name, data in zip(("a.txt", "b.txt"), contents, strict=True)
    ]
    desc = {
        "schema": "engineering.research-publication.v1",
        "producer_workspace": "/producer",
        "run_id": "observed",
        "project": "study",
        "lab": "source",
        "experiment": "sample",
        "scope": "readings",
        "plan_sha256": "a" * 64,
        "verification_seq": 2,
        "workspace_revision": 0,
        "files": files,
    }
    sha = publication_hash(desc)
    folder = tmp_path / "research-inputs" / sha
    fetched: list[int] = []

    def fetch(index: int) -> bytes:
        if index == 1 and not fetched.count(1):
            fetched.append(index)
            raise ConnectionError("stream interrupted")
        fetched.append(index)
        return contents[index]

    owner = EngineeringRunService(tmp_path)

    def transfer() -> dict:
        return owner.import_research_publication(
            workspace=tmp_path, digest=sha, descriptor=desc, fetch=fetch, scope=_scope(tmp_path)
        )

    with owner.store.acquire_workspace(tmp_path), pytest.raises(BlockingIOError, match="active writer"):
        transfer()
    assert not folder.exists()
    with pytest.raises(ConnectionError, match="interrupted"):
        transfer()
    assert not (folder / "manifest.json").exists()
    result = transfer()
    assert fetched == [0, 1, 1]
    committed = (tmp_path / result["report"]["manifest"]).read_bytes()
    (folder / "intent.json").unlink()
    with pytest.raises(ValueError, match="intent"):
        transfer()
    assert (folder / "manifest.json").read_bytes() == committed and not (folder / "intent.json").exists()
    assert fetched == [0, 1, 1]


async def test_verified_publication_is_issued_once_and_retrievable_after_live_drift(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = isolated_workspace
    scope = _scope(workspace)
    scripted_provider.client("openai", workspace, monkeypatch)
    source = workspace / "sample.txt"
    source.write_text("measurement", encoding="utf-8")
    plan = {
        "schema": "engineering.research-plan.v1",
        "project": "case",
        "experiments": [{"lab": "source", "id": "first", "scope": "a bounded reading"}],
    }
    owner = EngineeringRunService(workspace, permission_resolver=lambda _row: scope, require_qa=True)
    cmd = RoleRunCommand(
        "issued-run",
        "director",
        "Independently verify the saved measurement.",
        workspace,
        required_checks=("test -f sample.txt",),
        research={"plan": plan, "lab": "source"},
    )
    for response in (
        _text("Ready to check."),
        _calls(("inspect", "read_file", {"file": "sample.txt"})),
        _calls(("approve", "report_review", {"approved": True, "feedback": "Inspected the measurement."})),
    ):
        scripted_provider.enqueue_json(response)
    await owner.start(cmd, scope=scope, limits=RunLimits(time.time() + 45, 1000000, 64000))
    await owner.wait(cmd.session_id, workspace=workspace)
    assert owner.snapshot(cmd.session_id, workspace=workspace)["project_outcome"] == "accepted"
    issued = owner.publish_research(cmd.session_id, workspace=workspace, experiment="first", files=["sample.txt"])
    sha = issued["publication_sha256"]
    assert owner.research_publication(cmd.session_id, sha, workspace=workspace) == issued["descriptor"]
    source.write_text("new measurement", encoding="utf-8")
    assert owner.research_publication_file(cmd.session_id, sha, 0, workspace=workspace) == b"measurement"
    assert (
        owner.publish_research(
            cmd.session_id,
            workspace=workspace,
            experiment="first",
            files=["sample.txt"],
            verification_seq=issued["descriptor"]["verification_seq"],
        )
        == issued
    )
    assert len(owner.store.load_events(cmd.session_id, kinds=("research_publication_issued",))) == 1
    owner._permission_resolver = lambda _: PermissionScope((), (), frozenset(), False)
    with pytest.raises(PermissionError):
        owner.research_publication_file(cmd.session_id, sha, 0, workspace=workspace)
