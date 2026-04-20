"""Renderer for a generic resource HTTP service experiment project."""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TargetProjectManifest


class ResourceHttpServiceRenderer:
    """Render a traditional Python HTTP resource service from a target manifest."""

    def render(self, manifest: TargetProjectManifest) -> dict[str, str]:
        package_dir = manifest.package_name
        return {
            "tui_runtime.md": self._render_readme(manifest),
            "pyproject.toml": self._render_pyproject(manifest),
            ".env.example": self._render_env(manifest),
            f"{package_dir}/__init__.py": self._render_package_init(manifest),
            f"{package_dir}/__main__.py": self._render_main(manifest),
            f"{package_dir}/domain/__init__.py": "",
            f"{package_dir}/domain/models.py": self._render_models(manifest),
            f"{package_dir}/application/__init__.py": "",
            f"{package_dir}/application/config.py": self._render_config(manifest),
            f"{package_dir}/application/service.py": self._render_service(manifest),
            f"{package_dir}/infrastructure/__init__.py": "",
            f"{package_dir}/infrastructure/blob_store.py": self._render_blob_store(manifest),
            f"{package_dir}/infrastructure/index_store.py": self._render_index_store(manifest),
            f"{package_dir}/delivery/__init__.py": "",
            f"{package_dir}/delivery/http_api.py": self._render_http_api(manifest),
            f"{package_dir}/delivery/cli.py": self._render_cli(manifest),
            "tests/test_service.py": self._render_service_tests(manifest),
            "tests/test_http_api.py": self._render_http_tests(manifest),
            "data/.gitkeep": "",
            "storage/blobs/.gitkeep": "",
        }

    def _render_readme(self, manifest: TargetProjectManifest) -> str:
        commands = "\n".join(f"- `{item.name}`: {item.description}" for item in manifest.commands)
        return "\n".join(
            [
                f"# {manifest.project_title}",
                "",
                manifest.summary,
                "",
                "This traditional Python project is produced from Polaris Cell IR but runs as a normal repo with standard library only.",
                "",
                "## Capabilities",
                "",
                commands,
                "",
                "## Endpoints",
                "",
                "- `GET /health`",
                "- `GET /resources?query=<text>`",
                "- `GET /resources/<resource_id>`",
                "- `GET /resources/<resource_id>/download`",
                "- `POST /resources`",
                "- `DELETE /resources/<resource_id>`",
                "",
                "## Verify",
                "",
                "```bash",
                "python -m unittest discover -s tests -p test_*.py",
                "```",
                "",
            ]
        )

    def _render_pyproject(self, manifest: TargetProjectManifest) -> str:
        return (
            dedent(
                f"""
            [project]
            name = "{manifest.project_slug}"
            version = "0.1.0"
            description = "{manifest.summary}"
            readme = "tui_runtime.md"
            requires-python = ">=3.11"
            dependencies = []

            [build-system]
            requires = ["setuptools>=68"]
            build-backend = "setuptools.build_meta"
            """
            ).strip()
            + "\n"
        )

    def _render_env(self, manifest: TargetProjectManifest) -> str:
        return "\n".join(
            [
                f"RESOURCE_HOST={self._setting_text(manifest, 'host', '127.0.0.1')}",
                f"RESOURCE_PORT={self._setting_int(manifest, 'port', 8765)}",
                f"RESOURCE_METADATA_PATH={self._setting_text(manifest, 'metadata_path', 'data/resource_index.json')}",
                f"RESOURCE_BLOB_ROOT={self._setting_text(manifest, 'blob_root', 'storage/blobs')}",
                f"RESOURCE_MAX_PAYLOAD_BYTES={self._setting_int(manifest, 'max_payload_bytes', 10485760)}",
                f"RESOURCE_ENABLE_CHECKSUM={'1' if self._setting_bool(manifest, 'enable_checksum', True) else '0'}",
                "",
            ]
        )

    def _render_package_init(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        return "\n".join(
            [
                f'"""{manifest.project_title} package."""',
                "",
                f"from .application.service import {class_name}Service",
                "",
                f"__all__ = ['{class_name}Service']",
                "",
            ]
        )

    def _render_main(self, manifest: TargetProjectManifest) -> str:
        return "\n".join(
            [
                f"from {manifest.package_name}.delivery.cli import main",
                "",
                'if __name__ == "__main__":',
                "    raise SystemExit(main())",
                "",
            ]
        )

    def _render_models(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        return (
            dedent(
                f"""
            from __future__ import annotations

            from dataclasses import dataclass, field
            from typing import Any


            # Import time utility from Polaris canonical location
            from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso


            def _require_text(name: str, value: str) -> str:
                normalized = str(value or "").strip()
                if not normalized:
                    raise ValueError(f"{{name}} must be a non-empty string")
                return normalized


            def _normalize_tags(value: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
                items: list[str] = []
                for raw in value or ():
                    item = str(raw or "").strip()
                    if item:
                        items.append(item)
                return tuple(dict.fromkeys(items))


            @dataclass(frozen=True)
            class {entity.class_name}:
                {entity.record_id_field}: str
                filename: str
                content_type: str
                size_bytes: int
                checksum: str = ""
                tags: tuple[str, ...] = field(default_factory=tuple)
                deleted: bool = False
                created_at: str = field(default_factory=_utc_now_iso)
                updated_at: str = field(default_factory=_utc_now_iso)

                def __post_init__(self) -> None:
                    object.__setattr__(self, "{entity.record_id_field}", _require_text("{entity.record_id_field}", self.{entity.record_id_field}))
                    object.__setattr__(self, "filename", _require_text("filename", self.filename))
                    object.__setattr__(self, "content_type", _require_text("content_type", self.content_type))
                    object.__setattr__(self, "size_bytes", max(int(self.size_bytes), 0))
                    object.__setattr__(self, "checksum", str(self.checksum or "").strip())
                    object.__setattr__(self, "tags", _normalize_tags(self.tags))
                    object.__setattr__(self, "deleted", bool(self.deleted))
                    object.__setattr__(self, "created_at", _require_text("created_at", self.created_at))
                    object.__setattr__(self, "updated_at", _require_text("updated_at", self.updated_at))

                def to_dict(self) -> dict[str, Any]:
                    return {{
                        "{entity.record_id_field}": self.{entity.record_id_field},
                        "filename": self.filename,
                        "content_type": self.content_type,
                        "size_bytes": self.size_bytes,
                        "checksum": self.checksum,
                        "tags": list(self.tags),
                        "deleted": self.deleted,
                        "created_at": self.created_at,
                        "updated_at": self.updated_at,
                    }}

                @classmethod
                def from_dict(cls, payload: dict[str, Any]) -> "{entity.class_name}":
                    if not isinstance(payload, dict):
                        raise TypeError("payload must be a dict")
                    return cls(
                        {entity.record_id_field}=str(payload.get("{entity.record_id_field}", "")),
                        filename=str(payload.get("filename", "")),
                        content_type=str(payload.get("content_type", "")),
                        size_bytes=int(payload.get("size_bytes", 0)),
                        checksum=str(payload.get("checksum", "")),
                        tags=tuple(payload.get("tags") or ()),
                        deleted=bool(payload.get("deleted", False)),
                        created_at=str(payload.get("created_at", "")),
                        updated_at=str(payload.get("updated_at", "")),
                    )


            __all__ = ["{entity.class_name}"]
            """
            ).strip()
            + "\n"
        )

    def _render_config(self, manifest: TargetProjectManifest) -> str:
        return (
            dedent(
                f"""
            from __future__ import annotations

            from dataclasses import dataclass
            from pathlib import Path


            @dataclass(frozen=True)
            class AppConfig:
                metadata_path: Path
                blob_root: Path
                host: str
                port: int
                max_payload_bytes: int
                enable_checksum: bool = True

                def __post_init__(self) -> None:
                    object.__setattr__(self, "metadata_path", Path(self.metadata_path))
                    object.__setattr__(self, "blob_root", Path(self.blob_root))
                    host = str(self.host or "").strip()
                    if not host:
                        raise ValueError("host must be a non-empty string")
                    object.__setattr__(self, "host", host)
                    port = int(self.port)
                    if port < 0 or port > 65535:
                        raise ValueError("port must be between 0 and 65535")
                    object.__setattr__(self, "port", port)
                    max_payload_bytes = int(self.max_payload_bytes)
                    if max_payload_bytes < 1:
                        raise ValueError("max_payload_bytes must be >= 1")
                    object.__setattr__(self, "max_payload_bytes", max_payload_bytes)
                    object.__setattr__(self, "enable_checksum", bool(self.enable_checksum))


            def build_config() -> AppConfig:
                return AppConfig(
                    metadata_path=Path("{self._setting_text(manifest, "metadata_path", "data/resource_index.json")}"),
                    blob_root=Path("{self._setting_text(manifest, "blob_root", "storage/blobs")}"),
                    host="{self._setting_text(manifest, "host", "127.0.0.1")}",
                    port={self._setting_int(manifest, "port", 8765)},
                    max_payload_bytes={self._setting_int(manifest, "max_payload_bytes", 10485760)},
                    enable_checksum={self._setting_bool(manifest, "enable_checksum", True)},
                )


            __all__ = ["AppConfig", "build_config"]
            """
            ).strip()
            + "\n"
        )

    def _render_blob_store(self, manifest: TargetProjectManifest) -> str:
        return (
            dedent(
                """
            from __future__ import annotations

            from pathlib import Path


            class BlobStore:
                def __init__(self, blob_root: Path) -> None:
                    self._blob_root = Path(blob_root)
                    self._blob_root.mkdir(parents=True, exist_ok=True)

                def _path_for(self, resource_id: str) -> Path:
                    normalized = str(resource_id or "").strip()
                    if not normalized:
                        raise ValueError("resource_id must be a non-empty string")
                    return self._blob_root / f"{normalized}.bin"

                def write(self, resource_id: str, payload: bytes) -> Path:
                    path = self._path_for(resource_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(bytes(payload))
                    return path

                def read(self, resource_id: str) -> bytes:
                    path = self._path_for(resource_id)
                    if not path.exists():
                        raise FileNotFoundError(resource_id)
                    return path.read_bytes()


            __all__ = ["BlobStore"]
            """
            ).strip()
            + "\n"
        )

    def _render_index_store(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        record_id = manifest.entity.record_id_field
        return (
            dedent(
                f"""
            from __future__ import annotations

            import json
            from pathlib import Path
            from typing import Iterable

            from {manifest.package_name}.domain.models import {class_name}


            class JsonIndexStore:
                def __init__(self, metadata_path: Path) -> None:
                    self._metadata_path = Path(metadata_path)
                    self._metadata_path.parent.mkdir(parents=True, exist_ok=True)

                def list_records(self) -> list[{class_name}]:
                    if not self._metadata_path.exists():
                        return []
                    payload = json.loads(self._metadata_path.read_text(encoding="utf-8"))
                    if not isinstance(payload, list):
                        raise ValueError("metadata payload must be a list")
                    return [{class_name}.from_dict(item) for item in payload]

                def save_records(self, records: Iterable[{class_name}]) -> None:
                    payload = [record.to_dict() for record in records]
                    self._metadata_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\\n",
                        encoding="utf-8",
                    )

                def get_record(self, resource_id: str) -> {class_name} | None:
                    for record in self.list_records():
                        if record.{record_id} == resource_id:
                            return record
                    return None

                def upsert(self, record: {class_name}) -> None:
                    items = self.list_records()
                    result: list[{class_name}] = []
                    updated = False
                    for item in items:
                        if item.{record_id} == record.{record_id}:
                            result.append(record)
                            updated = True
                        else:
                            result.append(item)
                    if not updated:
                        result.append(record)
                    self.save_records(result)


            __all__ = ["JsonIndexStore"]
            """
            ).strip()
            + "\n"
        )

    def _render_service(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        record_id = manifest.entity.record_id_field
        return (
            dedent(
                f"""
            from __future__ import annotations

            import hashlib
            import uuid
            from typing import Iterable

            from {manifest.package_name}.domain.models import {class_name}, _utc_now_iso
            from {manifest.package_name}.infrastructure.blob_store import BlobStore
            from {manifest.package_name}.infrastructure.index_store import JsonIndexStore


            class {class_name}Service:
                def __init__(
                    self,
                    index_store: JsonIndexStore,
                    blob_store: BlobStore,
                    *,
                    max_payload_bytes: int,
                    enable_checksum: bool = True,
                ) -> None:
                    self._index_store = index_store
                    self._blob_store = blob_store
                    self._max_payload_bytes = max(int(max_payload_bytes), 1)
                    self._enable_checksum = bool(enable_checksum)

                def upload_resource(
                    self,
                    *,
                    filename: str,
                    payload: bytes,
                    content_type: str = "application/octet-stream",
                    tags: Iterable[str] | None = None,
                ) -> {class_name}:
                    normalized_filename = str(filename or "").strip()
                    if not normalized_filename:
                        raise ValueError("filename must be a non-empty string")
                    body = bytes(payload)
                    if len(body) > self._max_payload_bytes:
                        raise ValueError("payload exceeds configured limit")
                    resource_id = uuid.uuid4().hex
                    checksum = hashlib.sha256(body).hexdigest() if self._enable_checksum else ""
                    self._blob_store.write(resource_id, body)
                    record = {class_name}(
                        {record_id}=resource_id,
                        filename=normalized_filename,
                        content_type=str(content_type or "application/octet-stream").strip() or "application/octet-stream",
                        size_bytes=len(body),
                        checksum=checksum,
                        tags=tuple(tags or ()),
                        deleted=False,
                    )
                    self._index_store.upsert(record)
                    return record

                def list_resources(self, *, query: str = "", include_deleted: bool = False) -> list[{class_name}]:
                    normalized_query = str(query or "").strip().lower()
                    result: list[{class_name}] = []
                    for record in self._index_store.list_records():
                        if record.deleted and not include_deleted:
                            continue
                        if normalized_query:
                            haystack = " ".join([record.filename, record.content_type, record.checksum, *record.tags]).lower()
                            if normalized_query not in haystack:
                                continue
                        result.append(record)
                    return sorted(result, key=lambda item: item.created_at)

                def get_resource(self, resource_id: str, *, include_deleted: bool = False) -> {class_name}:
                    record = self._index_store.get_record(resource_id)
                    if record is None:
                        raise KeyError(resource_id)
                    if record.deleted and not include_deleted:
                        raise KeyError(resource_id)
                    return record

                def download_resource(self, resource_id: str) -> tuple[{class_name}, bytes]:
                    record = self.get_resource(resource_id)
                    return record, self._blob_store.read(resource_id)

                def delete_resource(self, resource_id: str) -> {class_name}:
                    record = self.get_resource(resource_id)
                    deleted = {class_name}(
                        {record_id}=record.{record_id},
                        filename=record.filename,
                        content_type=record.content_type,
                        size_bytes=record.size_bytes,
                        checksum=record.checksum,
                        tags=record.tags,
                        deleted=True,
                        created_at=record.created_at,
                        updated_at=_utc_now_iso(),
                    )
                    self._index_store.upsert(deleted)
                    return deleted


            __all__ = ["{class_name}Service"]
            """
            ).strip()
            + "\n"
        )

    def _render_http_api(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        return (
            dedent(
                f"""
            from __future__ import annotations

            import base64
            import json
            from dataclasses import dataclass
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from typing import Any
            from urllib.parse import parse_qs, urlparse

            from {manifest.package_name}.application.config import AppConfig, build_config
            from {manifest.package_name}.application.service import {class_name}Service
            from {manifest.package_name}.infrastructure.blob_store import BlobStore
            from {manifest.package_name}.infrastructure.index_store import JsonIndexStore


            @dataclass(frozen=True)
            class AppRuntime:
                config: AppConfig
                service: {class_name}Service


            def build_runtime() -> AppRuntime:
                config = build_config()
                service = {class_name}Service(
                    JsonIndexStore(config.metadata_path),
                    BlobStore(config.blob_root),
                    max_payload_bytes=config.max_payload_bytes,
                    enable_checksum=config.enable_checksum,
                )
                return AppRuntime(config=config, service=service)


            class ResourceHandler(BaseHTTPRequestHandler):
                server_version = "ResourceHttpService/0.1"

                @property
                def runtime(self) -> AppRuntime:
                    return getattr(self.server, "runtime")

                def log_message(self, format: str, *args: object) -> None:
                    return

                def do_GET(self) -> None:
                    self._dispatch("GET")

                def do_POST(self) -> None:
                    self._dispatch("POST")

                def do_DELETE(self) -> None:
                    self._dispatch("DELETE")

                def _dispatch(self, method: str) -> None:
                    parsed = urlparse(self.path)
                    segments = [segment for segment in parsed.path.split("/") if segment]
                    try:
                        if parsed.path == "/health" and method == "GET":
                            self._send_json(200, {{"ok": True, "port": self.server.server_address[1]}})
                            return
                        if len(segments) == 1 and segments[0] == "resources" and method == "GET":
                            query_text = parse_qs(parsed.query).get("query", [""])[0]
                            items = [item.to_dict() for item in self.runtime.service.list_resources(query=query_text)]
                            self._send_json(200, {{"items": items}})
                            return
                        if len(segments) == 1 and segments[0] == "resources" and method == "POST":
                            payload = self._read_json_payload()
                            payload_b64 = str(payload.get("payload_b64") or "").strip()
                            if not payload_b64:
                                raise ValueError("payload_b64 is required")
                            body = base64.b64decode(payload_b64.encode("utf-8"))
                            record = self.runtime.service.upload_resource(
                                filename=str(payload.get("filename") or ""),
                                payload=body,
                                content_type=str(payload.get("content_type") or "application/octet-stream"),
                                tags=payload.get("tags") or (),
                            )
                            self._send_json(201, record.to_dict())
                            return
                        if len(segments) == 2 and segments[0] == "resources" and method == "GET":
                            record = self.runtime.service.get_resource(segments[1])
                            self._send_json(200, record.to_dict())
                            return
                        if len(segments) == 3 and segments[0] == "resources" and segments[2] == "download" and method == "GET":
                            record, body = self.runtime.service.download_resource(segments[1])
                            self._send_bytes(200, body, content_type=record.content_type, filename=record.filename)
                            return
                        if len(segments) == 2 and segments[0] == "resources" and method == "DELETE":
                            record = self.runtime.service.delete_resource(segments[1])
                            self._send_json(200, record.to_dict())
                            return
                        self._send_json(404, {{"error": "not_found"}})
                    except KeyError as exc:
                        self._send_json(404, {{"error": "resource_not_found", "resource_id": str(exc)}})
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        self._send_json(400, {{"error": "bad_request", "message": str(exc)}})

                def _read_json_payload(self) -> dict[str, Any]:
                    content_length = int(self.headers.get("Content-Length") or "0")
                    raw_body = self.rfile.read(content_length) if content_length > 0 else b"{{}}"
                    payload = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("JSON payload must be an object")
                    return payload

                def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(status_code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def _send_bytes(self, status_code: int, payload: bytes, *, content_type: str, filename: str) -> None:
                    self.send_response(status_code)
                    self.send_header("Content-Type", str(content_type or "application/octet-stream"))
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Content-Disposition", f'attachment; filename="{{filename}}"')
                    self.end_headers()
                    self.wfile.write(payload)


            def create_server(runtime: AppRuntime) -> ThreadingHTTPServer:
                server = ThreadingHTTPServer((runtime.config.host, runtime.config.port), ResourceHandler)
                setattr(server, "runtime", runtime)
                return server


            __all__ = ["AppRuntime", "ResourceHandler", "build_runtime", "create_server"]
            """
            ).strip()
            + "\n"
        )

    def _render_cli(self, manifest: TargetProjectManifest) -> str:
        return (
            dedent(
                f"""
            from __future__ import annotations

            import argparse
            import json

            from {manifest.package_name}.delivery.http_api import build_runtime, create_server


            def build_parser() -> argparse.ArgumentParser:
                parser = argparse.ArgumentParser(description="Operate the generated resource HTTP service")
                subparsers = parser.add_subparsers(dest="command", required=True)

                serve_parser = subparsers.add_parser("serve", help="Run the HTTP service")
                serve_parser.set_defaults(handler=handle_serve)

                list_parser = subparsers.add_parser("list", help="List indexed resources")
                list_parser.add_argument("--query", default="", help="Optional search query")
                list_parser.set_defaults(handler=handle_list)

                return parser


            def handle_serve(args: argparse.Namespace) -> int:
                runtime = build_runtime()
                server = create_server(runtime)
                print(f"Serving on {{server.server_address[0]}}:{{server.server_address[1]}}")
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    return 0
                finally:
                    server.server_close()
                return 0


            def handle_list(args: argparse.Namespace) -> int:
                runtime = build_runtime()
                items = [item.to_dict() for item in runtime.service.list_resources(query=str(args.query or ""))]
                print(json.dumps(items, ensure_ascii=False, indent=2))
                return 0


            def main(argv: list[str] | None = None) -> int:
                parser = build_parser()
                args = parser.parse_args(argv)
                return int(args.handler(args))


            if __name__ == "__main__":
                raise SystemExit(main())
            """
            ).strip()
            + "\n"
        )

    def _render_service_tests(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        record_id = manifest.entity.record_id_field
        return (
            dedent(
                f"""
            from __future__ import annotations

            import tempfile
            from pathlib import Path
            from unittest import TestCase

            from {manifest.package_name}.application.service import {class_name}Service
            from {manifest.package_name}.infrastructure.blob_store import BlobStore
            from {manifest.package_name}.infrastructure.index_store import JsonIndexStore


            class ResourceServiceTest(TestCase):
                def setUp(self) -> None:
                    self._tmpdir = tempfile.TemporaryDirectory()
                    root = Path(self._tmpdir.name)
                    self.service = {class_name}Service(
                        JsonIndexStore(root / "data" / "index.json"),
                        BlobStore(root / "storage" / "blobs"),
                        max_payload_bytes=1024 * 1024,
                        enable_checksum=True,
                    )

                def tearDown(self) -> None:
                    self._tmpdir.cleanup()

                def test_upload_list_download_and_delete(self) -> None:
                    created = self.service.upload_resource(
                        filename="hello.txt",
                        payload=b"hello world",
                        content_type="text/plain",
                        tags=["docs", "demo"],
                    )
                    self.assertEqual(created.filename, "hello.txt")
                    self.assertEqual(created.size_bytes, 11)
                    self.assertTrue(created.checksum)

                    listed = self.service.list_resources()
                    self.assertEqual(len(listed), 1)
                    self.assertEqual(listed[0].{record_id}, created.{record_id})

                    filtered = self.service.list_resources(query="docs")
                    self.assertEqual(len(filtered), 1)

                    record, payload = self.service.download_resource(created.{record_id})
                    self.assertEqual(record.{record_id}, created.{record_id})
                    self.assertEqual(payload, b"hello world")

                    deleted = self.service.delete_resource(created.{record_id})
                    self.assertTrue(deleted.deleted)
                    self.assertEqual(self.service.list_resources(), [])
            """
            ).strip()
            + "\n"
        )

    def _render_http_tests(self, manifest: TargetProjectManifest) -> str:
        class_name = manifest.entity.class_name
        record_id = manifest.entity.record_id_field
        return (
            dedent(
                f"""
            from __future__ import annotations

            import base64
            import http.client
            import json
            import tempfile
            import threading
            from pathlib import Path
            from unittest import TestCase

            from {manifest.package_name}.application.config import AppConfig
            from {manifest.package_name}.application.service import {class_name}Service
            from {manifest.package_name}.delivery.http_api import AppRuntime, create_server
            from {manifest.package_name}.infrastructure.blob_store import BlobStore
            from {manifest.package_name}.infrastructure.index_store import JsonIndexStore


            class ResourceHttpApiTest(TestCase):
                def setUp(self) -> None:
                    self._tmpdir = tempfile.TemporaryDirectory()
                    root = Path(self._tmpdir.name)
                    runtime = AppRuntime(
                        config=AppConfig(
                            metadata_path=root / "data" / "index.json",
                            blob_root=root / "storage" / "blobs",
                            host="127.0.0.1",
                            port=0,
                            max_payload_bytes=1024 * 1024,
                            enable_checksum=True,
                        ),
                        service={class_name}Service(
                            JsonIndexStore(root / "data" / "index.json"),
                            BlobStore(root / "storage" / "blobs"),
                            max_payload_bytes=1024 * 1024,
                            enable_checksum=True,
                        ),
                    )
                    self.server = create_server(runtime)
                    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                    self.thread.start()

                def tearDown(self) -> None:
                    self.server.shutdown()
                    self.server.server_close()
                    self.thread.join(timeout=5)
                    self._tmpdir.cleanup()

                def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> tuple[int, bytes, dict[str, str]]:
                    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
                    body = b""
                    headers: dict[str, str] = {{}}
                    if payload is not None:
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        headers["Content-Type"] = "application/json; charset=utf-8"
                    connection.request(method, path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                    result_headers = {{key.lower(): value for key, value in response.getheaders()}}
                    connection.close()
                    return response.status, data, result_headers

                def test_health_upload_list_download_delete(self) -> None:
                    status, payload, _ = self._request("GET", "/health")
                    self.assertEqual(status, 200)
                    self.assertTrue(json.loads(payload.decode("utf-8"))["ok"])

                    body = b"resource payload"
                    status, payload, _ = self._request(
                        "POST",
                        "/resources",
                        {{
                            "filename": "sample.txt",
                            "content_type": "text/plain",
                            "payload_b64": base64.b64encode(body).decode("utf-8"),
                            "tags": ["demo"],
                        }},
                    )
                    self.assertEqual(status, 201)
                    created = json.loads(payload.decode("utf-8"))
                    resource_id = created["{record_id}"]

                    status, payload, _ = self._request("GET", "/resources?query=demo")
                    self.assertEqual(status, 200)
                    items = json.loads(payload.decode("utf-8"))["items"]
                    self.assertEqual(len(items), 1)
                    self.assertEqual(items[0]["{record_id}"], resource_id)

                    status, payload, headers = self._request("GET", f"/resources/{{resource_id}}/download")
                    self.assertEqual(status, 200)
                    self.assertEqual(payload, body)
                    self.assertEqual(headers["content-type"], "text/plain")

                    status, payload, _ = self._request("DELETE", f"/resources/{{resource_id}}")
                    self.assertEqual(status, 200)
                    deleted = json.loads(payload.decode("utf-8"))
                    self.assertTrue(deleted["deleted"])
            """
            ).strip()
            + "\n"
        )

    def _setting_text(self, manifest: TargetProjectManifest, key: str, default: str) -> str:
        value = manifest.settings.get(key, default)
        return str(value or default)

    def _setting_int(self, manifest: TargetProjectManifest, key: str, default: int) -> int:
        value = manifest.settings.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _setting_bool(self, manifest: TargetProjectManifest, key: str, default: bool) -> bool:
        value = manifest.settings.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"", "0", "false", "no"}


__all__ = ["ResourceHttpServiceRenderer"]
