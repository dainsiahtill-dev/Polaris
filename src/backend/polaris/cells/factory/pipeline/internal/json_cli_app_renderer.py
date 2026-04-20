"""Generic renderer for JSON-backed CLI experiment projects.

The renderer intentionally stays generic: target-specific semantics are supplied
through a manifest at runtime instead of checked-in business code templates.
"""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import FieldSpec, TargetProjectManifest


class JsonCliAppRenderer:
    """Render a traditional Python project from a target manifest."""

    def render(self, manifest: TargetProjectManifest) -> dict[str, str]:
        package_dir = manifest.package_name
        return {
            "tui_runtime.md": self._render_readme(manifest),
            "pyproject.toml": self._render_pyproject(manifest),
            f"{package_dir}/__init__.py": self._render_package_init(manifest),
            f"{package_dir}/__main__.py": self._render_main(manifest),
            f"{package_dir}/domain/__init__.py": "",
            f"{package_dir}/domain/models.py": self._render_models(manifest),
            f"{package_dir}/application/__init__.py": "",
            f"{package_dir}/application/service.py": self._render_service(manifest),
            f"{package_dir}/infrastructure/__init__.py": "",
            f"{package_dir}/infrastructure/json_store.py": self._render_store(manifest),
            f"{package_dir}/delivery/__init__.py": "",
            f"{package_dir}/delivery/cli.py": self._render_cli(manifest),
            "tests/test_app.py": self._render_tests(manifest),
            "data/.gitkeep": "",
        }

    def _render_readme(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        commands = "\n".join(f"- `{item.name}`: {item.description}" for item in manifest.commands)
        searchable = ", ".join(field.name for field in entity.searchable_fields) or "无"
        return "\n".join(
            [
                f"# {manifest.project_title}",
                "",
                manifest.summary,
                "",
                "这是一个由 Polaris 实验投影链生成的传统 Python 子项目。项目本身不依赖 Polaris 运行时，直接使用标准 Python 目录与命令行接口。",
                "",
                "## 功能",
                "",
                commands,
                "",
                "## 数据模型",
                "",
                f"- 主实体: `{entity.class_name}`",
                f"- 可搜索字段: {searchable}",
                "- 存储方式: 本地 JSON 文件",
                "",
                "## 运行",
                "",
                "```bash",
                f"python -m {manifest.package_name}.delivery.cli list --storage data/{entity.plural}.json",
                f'python -m {manifest.package_name}.delivery.cli add --title "示例" --content "内容" --tags work,idea',
                f"python -m {manifest.package_name}.delivery.cli search --query 示例 --storage data/{entity.plural}.json",
                "```",
                "",
                "## 测试",
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

    def _render_package_init(self, manifest: TargetProjectManifest) -> str:
        return self._to_text(
            [
                f'"""{manifest.project_title} package."""',
                "",
                f"from .application.service import {manifest.entity.class_name}Service",
                f"from .infrastructure.json_store import Json{manifest.entity.class_name}Store",
                "",
                f"__all__ = ['{manifest.entity.class_name}Service', 'Json{manifest.entity.class_name}Store']",
            ]
        )

    def _render_main(self, manifest: TargetProjectManifest) -> str:
        return self._to_text(
            [
                f"from {manifest.package_name}.delivery.cli import main",
                "",
                'if __name__ == "__main__":',
                "    raise SystemExit(main())",
            ]
        )

    def _render_models(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        lines = [
            "from __future__ import annotations",
            "",
            "from dataclasses import dataclass, field",
            "from typing import Any",
            "",
            "",
            "# Import time utility from Polaris canonical location",
            "from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso",
            "",
            "",
            "def _normalize_required_text(name: str, value: str) -> str:",
            "    normalized = str(value or '').strip()",
            "    if not normalized:",
            "        raise ValueError(f'{name} must be a non-empty string')",
            "    return normalized",
            "",
            "",
            "def _normalize_tags(value: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:",
            "    normalized: list[str] = []",
            "    for raw in value or ():",
            "        tag = str(raw or '').strip()",
            "        if tag:",
            "            normalized.append(tag)",
            "    return tuple(dict.fromkeys(normalized))",
            "",
            "",
            "@dataclass(frozen=True)",
            f"class {entity.class_name}:",
            f"    {entity.record_id_field}: str",
        ]
        for item in entity.fields:
            if item.kind == "str":
                lines.append(f"    {item.name}: str")
            elif item.kind == "tags":
                lines.append(f"    {item.name}: tuple[str, ...] = field(default_factory=tuple)")
            elif item.kind == "bool":
                lines.append(f"    {item.name}: bool = False")
        lines.extend(
            [
                "    created_at: str = field(default_factory=_utc_now_iso)",
                "    updated_at: str = field(default_factory=_utc_now_iso)",
                "",
                "    def __post_init__(self) -> None:",
                f"        object.__setattr__(self, '{entity.record_id_field}', _normalize_required_text('{entity.record_id_field}', self.{entity.record_id_field}))",
            ]
        )
        for item in entity.fields:
            if item.kind == "str":
                lines.append(
                    f"        object.__setattr__(self, '{item.name}', _normalize_required_text('{item.name}', self.{item.name}))"
                )
            elif item.kind == "tags":
                lines.append(f"        object.__setattr__(self, '{item.name}', _normalize_tags(self.{item.name}))")
        lines.extend(
            [
                "        object.__setattr__(self, 'created_at', _normalize_required_text('created_at', self.created_at))",
                "        object.__setattr__(self, 'updated_at', _normalize_required_text('updated_at', self.updated_at))",
                "",
                "    def to_dict(self) -> dict[str, Any]:",
                "        return {",
                f"            '{entity.record_id_field}': self.{entity.record_id_field},",
            ]
        )
        for item in entity.fields:
            if item.kind == "tags":
                lines.append(f"            '{item.name}': list(self.{item.name}),")
            else:
                lines.append(f"            '{item.name}': self.{item.name},")
        lines.extend(
            [
                "            'created_at': self.created_at,",
                "            'updated_at': self.updated_at,",
                "        }",
                "",
                "    @classmethod",
                f"    def from_dict(cls, payload: dict[str, Any]) -> '{entity.class_name}':",
                "        if not isinstance(payload, dict):",
                "            raise TypeError('payload must be a dict')",
                "        return cls(",
                f"            {entity.record_id_field}=str(payload.get('{entity.record_id_field}', '')),",
            ]
        )
        for item in entity.fields:
            if item.kind == "tags":
                lines.append(f"            {item.name}=tuple(payload.get('{item.name}') or ()),")
            elif item.kind == "bool":
                lines.append(f"            {item.name}=bool(payload.get('{item.name}', False)),")
            else:
                lines.append(f"            {item.name}=str(payload.get('{item.name}', '')),")
        lines.extend(
            [
                "            created_at=str(payload.get('created_at', '')),",
                "            updated_at=str(payload.get('updated_at', '')),",
                "        )",
                "",
                f"__all__ = ['{entity.class_name}']",
            ]
        )
        return self._to_text(lines)

    def _render_store(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        return self._to_text(
            [
                "from __future__ import annotations",
                "",
                "import json",
                "from pathlib import Path",
                "",
                f"from {manifest.package_name}.domain.models import {entity.class_name}",
                "",
                "",
                f"class Json{entity.class_name}Store:",
                f'    """Persist {entity.plural} as UTF-8 JSON."""',
                "",
                "    def __init__(self, storage_path: str | Path) -> None:",
                "        self._storage_path = Path(storage_path)",
                "",
                f"    def load(self) -> list[{entity.class_name}]:",
                "        if not self._storage_path.exists():",
                "            return []",
                "        raw = self._storage_path.read_text(encoding='utf-8')",
                "        payload = json.loads(raw or '[]')",
                "        if not isinstance(payload, list):",
                "            raise ValueError('storage payload must be a list')",
                "        return [",
                f"            {entity.class_name}.from_dict(item)",
                "            for item in payload",
                "            if isinstance(item, dict)",
                "        ]",
                "",
                f"    def save(self, records: list[{entity.class_name}]) -> None:",
                "        self._storage_path.parent.mkdir(parents=True, exist_ok=True)",
                "        payload = [record.to_dict() for record in records]",
                "        self._storage_path.write_text(",
                "            json.dumps(payload, ensure_ascii=False, indent=2) + '\\n',",
                "            encoding='utf-8',",
                "        )",
                "",
                f"__all__ = ['Json{entity.class_name}Store']",
            ]
        )

    def _render_service(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        searchable_fields = [item.name for item in entity.searchable_fields if item.kind == "str"]
        tags_field = next((item.name for item in entity.fields if item.kind == "tags"), None)
        archive_field = entity.archive_field
        create_params = ", ".join(self._service_parameter(item) for item in entity.fields if item.kind != "bool")
        lines = [
            "from __future__ import annotations",
            "",
            "import uuid",
            "",
            f"from {manifest.package_name}.domain.models import {entity.class_name}",
            f"from {manifest.package_name}.infrastructure.json_store import Json{entity.class_name}Store",
            "",
            "# Import time utility from Polaris canonical location",
            "from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso",
            "",
            "",
            f"class {entity.class_name}Service:",
            f'    """Application service for {entity.plural}."""',
            "",
            f"    def __init__(self, store: Json{entity.class_name}Store) -> None:",
            "        self._store = store",
            "",
            f"    def create_{entity.singular}(self, *, {create_params}) -> {entity.class_name}:",
            "        timestamp = _utc_now_iso()",
            f"        record = {entity.class_name}(",
            f"            {entity.record_id_field}=uuid.uuid4().hex,",
        ]
        for item in entity.fields:
            if item.kind == "bool":
                lines.append(f"            {item.name}=False,")
            else:
                lines.append(f"            {item.name}={item.name},")
        lines.extend(
            [
                "            created_at=timestamp,",
                "            updated_at=timestamp,",
                "        )",
                "        records = self._store.load()",
                "        records.append(record)",
                "        self._store.save(records)",
                "        return record",
                "",
                f"    def list_{entity.plural}(self, *, include_archived: bool = False) -> list[{entity.class_name}]:",
                "        records = self._store.load()",
            ]
        )
        if archive_field:
            lines.extend(
                [
                    "        if include_archived:",
                    "            return records",
                    f"        return [record for record in records if not record.{archive_field}]",
                ]
            )
        else:
            lines.append("        return records")
        lines.extend(
            [
                "",
                f"    def search_{entity.plural}(self, query: str, *, include_archived: bool = False) -> list[{entity.class_name}]:",
                "        search_text = str(query or '').strip().lower()",
                "        if not search_text:",
                f"            return self.list_{entity.plural}(include_archived=include_archived)",
                f"        results: list[{entity.class_name}] = []",
                f"        for record in self.list_{entity.plural}(include_archived=include_archived):",
                "            haystacks = [",
            ]
        )
        for field_name in searchable_fields:
            lines.append(f"                str(record.{field_name}),")
        lines.extend(
            [
                "            ]",
                "            if any(search_text in value.lower() for value in haystacks):",
                "                results.append(record)",
                "                continue",
            ]
        )
        if tags_field:
            lines.extend(
                [
                    f"            if any(search_text in tag.lower() for tag in record.{tags_field}):",
                    "                results.append(record)",
                ]
            )
        lines.extend(["        return results", ""])
        if archive_field:
            lines.extend(
                [
                    f"    def archive_{entity.singular}(self, {entity.record_id_field}: str) -> {entity.class_name}:",
                    f"        target_id = str({entity.record_id_field} or '').strip()",
                    "        timestamp = _utc_now_iso()",
                    "        updated: list[" + entity.class_name + "] = []",
                    f"        archived_record: {entity.class_name} | None = None",
                    "        for record in self._store.load():",
                    f"            if record.{entity.record_id_field} != target_id:",
                    "                updated.append(record)",
                    "                continue",
                    f"            archived_record = {entity.class_name}(",
                    f"                {entity.record_id_field}=record.{entity.record_id_field},",
                ]
            )
            for item in entity.fields:
                if item.name == archive_field:
                    lines.append(f"                {item.name}=True,")
                else:
                    lines.append(f"                {item.name}=record.{item.name},")
            lines.extend(
                [
                    "                created_at=record.created_at,",
                    "                updated_at=timestamp,",
                    "            )",
                    "            updated.append(archived_record)",
                    "        if archived_record is None:",
                    f"            raise ValueError('{entity.singular} not found')",
                    "        self._store.save(updated)",
                    "        return archived_record",
                    "",
                ]
            )
        lines.append(f"__all__ = ['{entity.class_name}Service']")
        return self._to_text(lines)

    def _render_cli(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        string_fields = [item.name for item in entity.fields if item.kind == "str"]
        title_field = string_fields[0]
        content_field = string_fields[1]
        tags_field = next((item.name for item in entity.fields if item.kind == "tags"), None)
        archive_field = entity.archive_field
        lines = [
            "from __future__ import annotations",
            "",
            "import argparse",
            "from pathlib import Path",
            "",
            f"from {manifest.package_name}.application.service import {entity.class_name}Service",
            f"from {manifest.package_name}.infrastructure.json_store import Json{entity.class_name}Store",
            "",
            "",
            f"def _build_service(storage: str) -> {entity.class_name}Service:",
            "    storage_path = Path(storage)",
            f"    return {entity.class_name}Service(Json{entity.class_name}Store(storage_path))",
            "",
            "",
            f"def _format_{entity.singular}(record) -> str:",
            f"    header = f'{{record.{entity.record_id_field}}} | {{record.{title_field}}}'",
        ]
        if tags_field:
            lines.extend(
                [
                    f"    if record.{tags_field}:",
                    f"        header += ' | tags=' + ', '.join(record.{tags_field})",
                ]
            )
        if archive_field:
            lines.extend([f"    if record.{archive_field}:", "        header += ' | archived'"])
        lines.extend(
            [
                f"    return header + f'\\n  {content_field}: {{record.{content_field}}}'",
                "",
                "",
                "def build_parser() -> argparse.ArgumentParser:",
                f"    parser = argparse.ArgumentParser(description='{manifest.project_title} CLI')",
                "    subparsers = parser.add_subparsers(dest='command', required=True)",
                "",
                "    add_parser = subparsers.add_parser('add', help='Create a new record')",
                f"    add_parser.add_argument('--storage', default='data/{entity.plural}.json', help='Path to the JSON storage file')",
                f"    add_parser.add_argument('--{title_field}', dest='{title_field}', required=True)",
                f"    add_parser.add_argument('--{content_field}', dest='{content_field}', required=True)",
            ]
        )
        if tags_field:
            lines.append(f"    add_parser.add_argument('--{tags_field}', dest='{tags_field}', default='')")
        lines.extend(
            [
                "",
                f"    list_parser = subparsers.add_parser('list', help='List existing {entity.plural}')",
                f"    list_parser.add_argument('--storage', default='data/{entity.plural}.json', help='Path to the JSON storage file')",
                "",
                f"    search_parser = subparsers.add_parser('search', help='Search {entity.plural}')",
                f"    search_parser.add_argument('--storage', default='data/{entity.plural}.json', help='Path to the JSON storage file')",
                "    search_parser.add_argument('--query', required=True)",
                "",
            ]
        )
        if archive_field:
            lines.extend(
                [
                    f"    archive_parser = subparsers.add_parser('archive', help='Archive a {entity.singular}')",
                    f"    archive_parser.add_argument('--storage', default='data/{entity.plural}.json', help='Path to the JSON storage file')",
                    f"    archive_parser.add_argument('--{entity.record_id_field}', dest='{entity.record_id_field}', required=True)",
                    "",
                ]
            )
        lines.extend(
            [
                "    return parser",
                "",
                "",
                "def main(argv: list[str] | None = None) -> int:",
                "    parser = build_parser()",
                "    args = parser.parse_args(argv)",
                "    service = _build_service(args.storage)",
                "",
                "    if args.command == 'add':",
            ]
        )
        add_call = [f"{title_field}=args.{title_field}", f"{content_field}=args.{content_field}"]
        if tags_field:
            add_call.append(
                f"{tags_field}=[item.strip() for item in str(args.{tags_field} or '').split(',') if item.strip()]"
            )
        lines.extend(
            [
                f"        record = service.create_{entity.singular}({', '.join(add_call)})",
                f"        print(_format_{entity.singular}(record))",
                "        return 0",
                "",
                "    if args.command == 'list':",
                f"        records = service.list_{entity.plural}()",
                "        if not records:",
                f"            print('No {entity.plural} found.')",
                "            return 0",
                "        for record in records:",
                f"            print(_format_{entity.singular}(record))",
                "        return 0",
                "",
                "    if args.command == 'search':",
                f"        records = service.search_{entity.plural}(args.query)",
                "        if not records:",
                f"            print('No matching {entity.plural} found.')",
                "            return 0",
                "        for record in records:",
                f"            print(_format_{entity.singular}(record))",
                "        return 0",
                "",
            ]
        )
        if archive_field:
            lines.extend(
                [
                    "    if args.command == 'archive':",
                    f"        record = service.archive_{entity.singular}(args.{entity.record_id_field})",
                    f"        print(_format_{entity.singular}(record))",
                    "        return 0",
                    "",
                ]
            )
        lines.extend(
            [
                "    parser.error('Unsupported command')",
                "    return 2",
                "",
                "",
                'if __name__ == "__main__":',
                "    raise SystemExit(main())",
            ]
        )
        return self._to_text(lines)

    def _render_tests(self, manifest: TargetProjectManifest) -> str:
        entity = manifest.entity
        string_fields = [item.name for item in entity.fields if item.kind == "str"]
        title_field = string_fields[0]
        content_field = string_fields[1]
        tags_field = next((item.name for item in entity.fields if item.kind == "tags"), None)
        archive_field = entity.archive_field
        create_kwargs = [f"{title_field}='First note'", f"{content_field}='Meeting summary'"]
        second_kwargs = [f"{title_field}='Reference draft'", f"{content_field}='Collect generic validation inputs'"]
        if tags_field:
            create_kwargs.append(f"{tags_field}=['work', 'summary']")
            second_kwargs.append(f"{tags_field}=['personal']")
        lines = [
            "from __future__ import annotations",
            "",
            "import tempfile",
            "import unittest",
            "from pathlib import Path",
            "",
            f"from {manifest.package_name}.application.service import {entity.class_name}Service",
            f"from {manifest.package_name}.infrastructure.json_store import Json{entity.class_name}Store",
            "",
            "",
            f"class {entity.class_name}ServiceTests(unittest.TestCase):",
            "    def setUp(self) -> None:",
            "        self._tmpdir = tempfile.TemporaryDirectory()",
            f"        storage_path = Path(self._tmpdir.name) / '{entity.plural}.json'",
            f"        self.service = {entity.class_name}Service(Json{entity.class_name}Store(storage_path))",
            "",
            "    def tearDown(self) -> None:",
            "        self._tmpdir.cleanup()",
            "",
            f"    def test_create_and_list_{entity.plural}(self) -> None:",
            f"        created = self.service.create_{entity.singular}({', '.join(create_kwargs)})",
            f"        records = self.service.list_{entity.plural}()",
            "        self.assertEqual(len(records), 1)",
            f"        self.assertEqual(records[0].{entity.record_id_field}, created.{entity.record_id_field})",
            "",
            f"    def test_search_{entity.plural}_matches_keywords_and_tags(self) -> None:",
            f"        self.service.create_{entity.singular}({', '.join(create_kwargs)})",
            f"        self.service.create_{entity.singular}({', '.join(second_kwargs)})",
            f"        by_title = self.service.search_{entity.plural}('meeting')",
            "        self.assertEqual(len(by_title), 1)",
        ]
        if tags_field:
            lines.extend(
                [
                    f"        by_tag = self.service.search_{entity.plural}('personal')",
                    "        self.assertEqual(len(by_tag), 1)",
                ]
            )
        if archive_field:
            lines.extend(
                [
                    "",
                    f"    def test_archive_{entity.singular}_hides_default_listing(self) -> None:",
                    f"        created = self.service.create_{entity.singular}({', '.join(create_kwargs)})",
                    f"        archived = self.service.archive_{entity.singular}(created.{entity.record_id_field})",
                    f"        self.assertTrue(archived.{archive_field})",
                    f"        active = self.service.list_{entity.plural}()",
                    "        self.assertEqual(active, [])",
                    f"        all_records = self.service.list_{entity.plural}(include_archived=True)",
                    "        self.assertEqual(len(all_records), 1)",
                ]
            )
        lines.extend(["", 'if __name__ == "__main__":', "    unittest.main()"])
        return self._to_text(lines)

    def _service_parameter(self, field: FieldSpec) -> str:
        if field.kind == "tags":
            return f"{field.name}: list[str] | tuple[str, ...] | None = None"
        return f"{field.name}: str"

    def _to_text(self, lines: list[str]) -> str:
        return "\n".join(lines) + "\n"


__all__ = ["JsonCliAppRenderer"]
