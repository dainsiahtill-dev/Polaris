from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import files


def _client(workspace: Path) -> TestClient:
    app = FastAPI()
    app.include_router(files.router)
    app.dependency_overrides[files.require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=str(workspace)))
    return TestClient(app, raise_server_exceptions=False)


def test_file_tree_lists_workspace_source_and_excludes_heavy_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const ok = true;\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "leftpad.js").write_text("module.exports = true;\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/tree")

    assert response.status_code == 200
    payload = response.json()
    paths = _collect_paths(payload["tree"])
    assert "src/index.ts" in paths
    assert "node_modules/leftpad.js" not in paths
    assert "node_modules" in payload["excluded"]
    assert payload["stats"]["files"] == 1
    assert payload["stats"]["omitted"] >= 1


def test_file_tree_can_include_ignored_dirs_on_explicit_request(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "leftpad.js").write_text("module.exports = true;\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/tree?include_ignored=true&max_entries=20")

    assert response.status_code == 200
    paths = _collect_paths(response.json()["tree"])
    assert "node_modules/leftpad.js" in paths


def test_file_tree_subdirectory_paths_remain_scope_relative(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const ok = true;\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/tree?root=src")

    assert response.status_code == 200
    payload = response.json()
    paths = _collect_paths(payload["tree"])
    assert payload["tree"]["path"] == "src"
    assert "src/index.ts" in paths
    read_response = client.get("/v2/files/read?scope=workspace&path=src/index.ts")
    assert read_response.status_code == 200
    assert "export const ok" in read_response.json()["content"]


def test_file_tree_respects_entry_budget_without_over_returning(tmp_path: Path) -> None:
    for index in range(20):
        (tmp_path / f"file_{index:02d}.txt").write_text(f"{index}\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/tree?max_entries=5")

    assert response.status_code == 200
    payload = response.json()
    paths = _collect_paths(payload["tree"])
    assert payload["truncated"] is True
    assert payload["stats"]["files"] == 5
    assert len(paths) == 5


def test_file_tree_classifies_common_extensionless_text_files(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM node:22\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("internal\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/tree")

    assert response.status_code == 200
    files = _collect_file_nodes(response.json()["tree"])
    assert files["Dockerfile"]["is_binary"] is False
    assert files["Dockerfile"]["language"] == "dockerfile"
    assert files["Makefile"]["is_binary"] is False
    assert files["Makefile"]["language"] == "makefile"
    assert files["LICENSE"]["is_binary"] is False


def test_read_workspace_scope_reads_real_workspace_relative_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/read?scope=workspace&path=src/main.py")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rel_path"] == "src/main.py"
    assert "print('hello')" in payload["content"]


def test_read_workspace_scope_head_mode_reads_file_start(tmp_path: Path) -> None:
    lines = [f"line-{index:03d}" for index in range(600)]
    (tmp_path / "large.txt").write_text("\n".join(lines), encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/read?scope=workspace&path=large.txt&tail_lines=0&max_chars=120&read_mode=head")

    assert response.status_code == 200
    payload = response.json()
    assert "line-000" in payload["content"]
    assert "line-599" not in payload["content"]


def test_read_workspace_scope_rejects_unknown_read_mode(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/read?scope=workspace&path=main.py&read_mode=middle")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_FILE_READ_MODE"


def test_read_workspace_scope_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    client = _client(tmp_path)

    response = client.get("/v2/files/read?scope=workspace&path=../outside-secret.txt")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PATH_OUTSIDE_WORKSPACE"


def _collect_paths(node: dict[str, object]) -> set[str]:
    paths: set[str] = set()
    path = str(node.get("path") or "")
    if path:
        paths.add(path)
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                paths.update(_collect_paths(child))
    return paths


def _collect_file_nodes(node: dict[str, object]) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    if node.get("type") == "file":
        files[str(node["name"])] = node
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                files.update(_collect_file_nodes(child))
    return files
