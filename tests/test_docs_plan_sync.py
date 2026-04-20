import importlib
import sys
from pathlib import Path


def _load_docs_router_module():
    repo_root = Path(__file__).resolve().parents[1]
    backend_root = repo_root / "src" / "backend"
    if str(backend_root) in sys.path:
        sys.path.remove(str(backend_root))
    sys.path.insert(0, str(backend_root))
    return importlib.import_module("app.routers.docs")


def test_sync_plan_to_runtime_skips_when_source_missing(tmp_path):
    docs_router = _load_docs_router_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    docs_router._sync_plan_to_runtime(str(workspace), "")

    runtime_plan = Path(
        docs_router.resolve_artifact_path(
            str(workspace),
            "",
            "runtime/contracts/plan.md",
        )
    )
    assert not runtime_plan.exists()


def test_sync_plan_to_runtime_copies_and_is_idempotent(tmp_path):
    docs_router = _load_docs_router_module()
    workspace = tmp_path / "workspace"
    plan_src = workspace / "docs" / "product" / "plan.md"
    plan_src.parent.mkdir(parents=True, exist_ok=True)
    plan_src.write_text("# Plan\n- item A\n", encoding="utf-8")

    docs_router._sync_plan_to_runtime(str(workspace), "")
    docs_router._sync_plan_to_runtime(str(workspace), "")

    runtime_plan = Path(
        docs_router.resolve_artifact_path(
            str(workspace),
            "",
            "runtime/contracts/plan.md",
        )
    )
    assert runtime_plan.exists()
    assert runtime_plan.read_text(encoding="utf-8") == "# Plan\n- item A\n"
    assert not runtime_plan.with_suffix(".md.tmp").exists()
