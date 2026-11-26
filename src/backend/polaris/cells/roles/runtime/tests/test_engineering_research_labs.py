import asyncio
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_research import project_lab
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_qa import _calls, _scope, _text


def publish(root: Path, lab: str, value: str) -> dict[str, Any]:
    evidence = root / (lab + ".txt")
    evidence.write_text(value, encoding="utf-8")
    manifest = root / (lab + ".json")
    manifest.write_text(
        json.dumps({"path": evidence.name, "digest": hashlib.sha256(evidence.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    return {
        "lab": lab,
        "id": "001",
        "scope": "declared sample",
        "next_question": "Compare the boundary case",
        "report": {
            "manifest": manifest.name,
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "references": [{"path_pointer": "/path", "digest_pointer": "/digest"}],
        },
        "depends_on": [],
    }


def test_three_labs_propagate_a_changed_input_only_to_its_consumers(tmp_path: Path) -> None:
    a, b, c = (publish(tmp_path, lab, lab) for lab in ("source", "display", "unrelated"))
    b["depends_on"] = [{"lab": "source", "id": "001", "sha256": a["report"]["sha256"]}]
    plan = {"schema": "engineering.research-plan.v1", "project": "example", "experiments": [b, c, a]}
    first = project_lab(tmp_path, plan, "display")
    assert first["state_counts"] == {"matched": 2}
    assert [n["key"] for n in first["experiments"]] == ["source/001", "display/001"]
    (tmp_path / "source.txt").write_text("changed", encoding="utf-8")
    second = project_lab(tmp_path, plan, "display")
    assert second["state_counts"] == {"changed": 1, "needs_recheck": 1}
    assert second["experiments"][1]["affected_by"] == ["source/001"]
    assert project_lab(tmp_path, plan, "unrelated")["state_counts"] == {"matched": 1}
    assert project_lab(tmp_path, plan, "display", read_allowed=lambda name: name != "source.txt")["state_counts"] == {
        "unproven": 1,
        "needs_recheck": 1,
    }
    a["report"]["sha256"] = "a" * 64
    assert project_lab(tmp_path, plan, "display")["experiments"][1]["affected_by"] == ["source/001"]


def test_plan_rejects_missing_duplicate_or_cyclic_identities(tmp_path: Path) -> None:
    lab = publish(tmp_path, "a", "a")
    plan = {"schema": "engineering.research-plan.v1", "project": "p", "experiments": [lab, lab]}
    with pytest.raises(ValueError, match="duplicate"):
        project_lab(tmp_path, plan, "a")
    plan["experiments"] = [lab]
    with pytest.raises(ValueError, match="unknown laboratory"):
        project_lab(tmp_path, plan, "absent")
    lab["depends_on"] = [{"lab": "a", "id": "001", "sha256": lab["report"]["sha256"]}]
    with pytest.raises(ValueError, match="cycle"):
        project_lab(tmp_path, plan, "a")


async def test_native_frames_and_cold_resume_recheck_the_frozen_lab_plan(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionConflictError

    root = isolated_workspace
    a, b = (publish(root, lab, lab) for lab in ("source", "display"))
    b["depends_on"] = [{"lab": "source", "id": "001", "sha256": a["report"]["sha256"]}]
    plan = {"schema": "engineering.research-plan.v1", "project": "case", "experiments": [a, b]}
    scope = _scope(root)
    scripted_provider.client("openai", root, monkeypatch)
    service = EngineeringRunService(root, require_qa=True, permission_resolver=lambda _row: scope)
    (root / "value.txt").write_text("2", encoding="utf-8")
    cmd = RoleRunCommand(
        "lab-one",
        "director",
        "Independently check the value.",
        root,
        required_checks=("test -f value.txt",),
        research={"lab": "display", "plan": plan},
    )
    limits = RunLimits(time.time() + 90, 1_000_000, 64_000)
    for response in (
        _text("Ready for inspection."),
        _calls(("read1", "read_file", {"file": "value.txt"})),
        _calls(("report1", "report_review", {"approved": True, "feedback": "Inspected the saved value."})),
    ):
        scripted_provider.enqueue_json(response)
    await service.start(cmd, scope=scope, limits=limits, request_key="key-lab")
    await service.wait(cmd.session_id, workspace=root)
    snapshot = service.snapshot(cmd.session_id, workspace=root)
    assert snapshot["research"]["lab"] == "display" and "plan" not in snapshot["research"]
    assert snapshot["qa_verdict"] == "approved"
    observations = service.store.load_events(cmd.session_id, kinds=("research_evidence_observed",))
    assert {e["role"] for e in observations} == {"qa", "director"}
    assert all(e["payload"]["state_counts"] == {"matched": 2} for e in observations)
    wire = [
        str(m.get("content"))
        for req in scripted_provider.requests
        for m in req["body"]["messages"]
        if m.get("role") == "user"
    ]
    assert any("Research evidence at observation time" in text and "source/001" in text for text in wire)
    with pytest.raises(EngineeringSessionConflictError):
        await service.start(
            replace(cmd, research={"lab": "source", "plan": plan}), scope=scope, limits=limits, request_key="key-lab"
        )
    # A saved interrupted run is resumed by a different service instance.
    other = replace(cmd, session_id="lab-two", research={"lab": "source", "plan": plan})
    service.store.create_run(
        other, scope=scope, limits=limits, engine_version=service.engine_version, request_key="key-two"
    )
    service.store.update_run(other.session_id, status="interrupted")
    (root / "source.txt").write_text("changed after interruption", encoding="utf-8")
    scripted_provider.enqueue_json(_text("The source version has changed."))
    child_code = """
import asyncio
import sys
from pathlib import Path
import pytest
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import ScriptedProvider
from polaris.cells.roles.runtime.tests.test_engineering_qa import _scope
root=Path(sys.argv[1])
provider=ScriptedProvider(base_url=sys.argv[2])
with pytest.MonkeyPatch.context() as mp:
    provider.client("openai", root, mp)
    service=EngineeringRunService(root,require_qa=False,permission_resolver=lambda _row:_scope(root))
    async def resume():
        await service.resume(sys.argv[3],workspace=root)
        await service.wait(sys.argv[3],workspace=root)
    asyncio.run(resume())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child_code,
        str(root),
        scripted_provider.base_url,
        other.session_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
    assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    resumed = service.store.load_events(other.session_id, kinds=("research_evidence_observed",))
    assert resumed[-1]["payload"]["state_counts"] == {"changed": 1}
    labs = service.research_laboratories("case", workspace=root)["laboratories"]
    assert {row["lab"]: row["session_id"] for row in labs} == {"source": "lab-two", "display": "lab-one"}
    assert service.store.previous_memory_run("lab-two", workspace=root) is None
    third = replace(other, session_id="lab-three")
    service.store.create_run(
        third, scope=scope, limits=limits, engine_version=service.engine_version, request_key="key-three"
    )
    prior = service.store.previous_memory_run("lab-three", workspace=root)
    assert prior is not None and prior["session_id"] == "lab-two"
    page = service.research_laboratories("case", workspace=root, lab="source", limit=1)
    assert [row["session_id"] for row in page["runs"]] == ["lab-three"] and page["next_offset"] == 1
    old = service.research_laboratories("case", workspace=root, lab="source", offset=1, limit=1)
    assert [row["session_id"] for row in old["runs"]] == ["lab-two"] and old["next_offset"] is None


def test_discovery_of_an_empty_workspace_and_missing_upstream(tmp_path: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_research import query_research_laboratories

    assert query_research_laboratories(tmp_path, "project")["laboratories"] == []
    assert not (tmp_path / ".polaris").exists()
    consumer = publish(tmp_path, "consumer", "on disk")
    consumer["depends_on"] = [{"lab": "missing", "id": "other", "sha256": "a" * 64}]
    plan = {"schema": "engineering.research-plan.v1", "project": "project", "experiments": [consumer]}
    view = project_lab(tmp_path, plan, "consumer")
    assert view["experiments"][0]["local"] == "matched"
    assert view["experiments"][0]["state"] == "needs_recheck"
    assert view["experiments"][0]["affected_by"] == ["missing/other"]
