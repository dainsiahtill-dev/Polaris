"""Document Manager - 尚书令文档管理器

文档版本控制、结构解析、差异分析和变更影响分析。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from difflib import unified_diff
from typing import Any

from polaris.delivery.cli.pm.state_manager import (
    _now_iso,
    _write_json_atomic,
    _write_text_atomic,
    get_state_manager,
)
from polaris.delivery.cli.pm.utils import read_json_file

logger = logging.getLogger(__name__)


@dataclass
class DocumentVersion:
    """文档版本"""

    version: str  # 版本号 (如 "v1.2.3")
    doc_path: str  # 文档路径
    checksum: str  # 内容校验和
    created_at: str = field(default_factory=_now_iso)
    created_by: str = "pm"  # 创建者
    change_summary: str = ""  # 变更摘要
    snapshot_path: str | None = None  # 快照文件路径


@dataclass
class ParsedRequirement:
    """解析出的需求点"""

    id: str
    title: str
    description: str
    section: str  # 所属章节
    line_start: int
    line_end: int


@dataclass
class ParsedInterface:
    """解析出的接口定义"""

    name: str
    interface_type: str  # api, function, class, etc.
    signature: str
    parameters: list[dict[str, str]]
    returns: str | None
    description: str
    section: str
    line_start: int
    line_end: int


@dataclass
class DocumentAnalysis:
    """文档分析结果"""

    doc_path: str
    analyzed_at: str = field(default_factory=_now_iso)
    requirements: list[ParsedRequirement] = field(default_factory=list)
    interfaces: list[ParsedInterface] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # 依赖的其他文档
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentDiff:
    """文档差异结果"""

    doc_path: str
    old_version: str
    new_version: str
    diff_text: str
    changed_sections: list[str] = field(default_factory=list)
    added_requirements: list[str] = field(default_factory=list)
    removed_requirements: list[str] = field(default_factory=list)
    modified_requirements: list[str] = field(default_factory=list)
    impact_score: float = 0.0  # 影响评分 0-1


class DocumentManager:
    """尚书令文档管理器

    核心功能：
    1. 文档版本控制 - 自动版本快照
    2. 文档结构解析 - 提取需求点、接口定义
    3. 文档差异分析 - 变更影响范围
    4. 文档关联分析 - 文档间的依赖关系

    存储结构：
        pm_data/documents/
        ├── versions.json          # 文档版本清单
        ├── snapshots/             # 文档快照
        │   └── {doc_hash}.md
        └── analysis/              # 文档分析结果
            ├── {doc_name}.json
            └── interfaces.json
    """

    VERSIONS_FILE = "versions.json"
    SNAPSHOTS_DIR = "snapshots"
    ANALYSIS_DIR = "analysis"
    INTERFACES_FILE = "interfaces.json"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.state_manager = get_state_manager(workspace)
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Ensure documents subsystem is initialized."""
        if not self.state_manager.is_initialized():
            self.state_manager.initialize()

    def _get_doc_dir(self) -> str:
        return self.state_manager.get_data_path("documents", "")

    def _compute_checksum(self, content: str) -> str:
        """计算内容校验和."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _load_versions(self) -> dict[str, Any]:
        """Load document versions registry."""
        data = self.state_manager.read_subsystem_data("documents", self.VERSIONS_FILE)
        if data is None:
            return {"version": "1.0", "documents": {}}
        return data

    def _save_versions(self, versions: dict[str, Any]) -> None:
        """Save document versions registry."""
        self.state_manager.write_subsystem_data("documents", self.VERSIONS_FILE, versions)

    def _get_snapshot_path(self, checksum: str) -> str:
        """Get snapshot file path."""
        return self.state_manager.get_data_path("documents", f"{self.SNAPSHOTS_DIR}/{checksum}.md")

    def _load_analysis(self, doc_name: str) -> DocumentAnalysis | None:
        """Load document analysis."""
        data = self.state_manager.read_subsystem_data("documents", f"{self.ANALYSIS_DIR}/{doc_name}.json")
        if data is None:
            return None

        requirements = [ParsedRequirement(**r) for r in data.get("requirements", [])]
        interfaces = [ParsedInterface(**i) for i in data.get("interfaces", [])]

        return DocumentAnalysis(
            doc_path=data["doc_path"],
            analyzed_at=data.get("analyzed_at", _now_iso()),
            requirements=requirements,
            interfaces=interfaces,
            dependencies=data.get("dependencies", []),
            metadata=data.get("metadata", {}),
        )

    def _save_analysis(self, doc_name: str, analysis: DocumentAnalysis) -> None:
        """Save document analysis."""
        data = {
            "doc_path": analysis.doc_path,
            "analyzed_at": analysis.analyzed_at,
            "requirements": [asdict(r) for r in analysis.requirements],
            "interfaces": [asdict(i) for i in analysis.interfaces],
            "dependencies": analysis.dependencies,
            "metadata": analysis.metadata,
        }
        self.state_manager.write_subsystem_data("documents", f"{self.ANALYSIS_DIR}/{doc_name}.json", data)

    def create_version(
        self,
        doc_path: str,
        content: str,
        created_by: str = "pm",
        change_summary: str = "",
    ) -> DocumentVersion:
        """Create a new document version.

        Args:
            doc_path: Document path
            content: Document content
            created_by: Who created the version
            change_summary: Change summary

        Returns:
            Document version info
        """
        checksum = self._compute_checksum(content)

        # Save snapshot
        snapshot_path = self._get_snapshot_path(checksum)
        _write_text_atomic(snapshot_path, content)

        # Generate version number
        versions = self._load_versions()
        doc_versions = versions["documents"].get(doc_path, {"versions": []})

        if doc_versions["versions"]:
            last_version = doc_versions["versions"][-1]["version"]
            # Parse version number
            match = re.match(r"v(\d+)\.(\d+)\.(\d+)", last_version)
            if match:
                major, minor, patch = map(int, match.groups())
                new_version = f"v{major}.{minor}.{patch + 1}"
            else:
                new_version = f"v1.0.{len(doc_versions['versions'])}"
        else:
            new_version = "v1.0.0"

        version_info = DocumentVersion(
            version=new_version,
            doc_path=doc_path,
            checksum=checksum,
            created_by=created_by,
            change_summary=change_summary,
            snapshot_path=snapshot_path,
        )

        # Update versions registry
        if doc_path not in versions["documents"]:
            versions["documents"][doc_path] = {"versions": [], "current": new_version}

        versions["documents"][doc_path]["versions"].append(asdict(version_info))
        versions["documents"][doc_path]["current"] = new_version
        self._save_versions(versions)

        # Record to history
        self.state_manager.append_to_history(
            "documents",
            {
                "action": "create_version",
                "doc_path": doc_path,
                "version": new_version,
                "checksum": checksum,
                "created_by": created_by,
                "change_summary": change_summary,
            },
        )

        return version_info

    def get_version(self, doc_path: str, version: str | None = None) -> DocumentVersion | None:
        """Get document version info.

        Args:
            doc_path: Document path
            version: Version number, defaults to current

        Returns:
            Document version or None
        """
        versions = self._load_versions()
        doc_data = versions["documents"].get(doc_path)
        if not doc_data:
            return None

        if version is None:
            version = doc_data.get("current", "v1.0.0")

        for v in doc_data.get("versions", []):
            if v["version"] == version:
                return DocumentVersion(**v)

        return None

    def get_version_content(self, doc_path: str, version: str | None = None) -> str | None:
        """Get document version content.

        Args:
            doc_path: Document path
            version: Version number, defaults to current

        Returns:
            Document content or None
        """
        version_info = self.get_version(doc_path, version)
        if not version_info or not version_info.snapshot_path:
            return None

        try:
            with open(version_info.snapshot_path, encoding="utf-8") as f:
                return f.read()
        except (RuntimeError, ValueError):
            return None

    def list_versions(self, doc_path: str) -> list[DocumentVersion]:
        """List all versions of a document.

        Args:
            doc_path: Document path

        Returns:
            List of versions
        """
        versions = self._load_versions()
        doc_data = versions["documents"].get(doc_path, {})
        return [DocumentVersion(**v) for v in doc_data.get("versions", [])]

    def compare_versions(self, doc_path: str, old_version: str, new_version: str) -> DocumentDiff:
        """Compare two document versions.

        Args:
            doc_path: Document path
            old_version: Old version number
            new_version: New version number

        Returns:
            Document diff result
        """
        old_content = self.get_version_content(doc_path, old_version) or ""
        new_content = self.get_version_content(doc_path, new_version) or ""

        # Generate unified diff
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = list(
            unified_diff(
                old_lines,
                new_lines,
                fromfile=f"{doc_path} ({old_version})",
                tofile=f"{doc_path} ({new_version})",
            )
        )

        diff_text = "".join(diff)

        # Parse diff to find changed sections
        changed_sections = self._extract_changed_sections(diff_text)

        # Analyze requirements changes
        old_reqs = {r.id for r in self._parse_requirements_inline(old_content)}
        new_reqs = {r.id for r in self._parse_requirements_inline(new_content)}

        added_reqs = list(new_reqs - old_reqs)
        removed_reqs = list(old_reqs - new_reqs)

        # Estimate impact score
        impact_score = self._calculate_impact_score(len(diff), len(old_content), len(added_reqs) + len(removed_reqs))

        return DocumentDiff(
            doc_path=doc_path,
            old_version=old_version,
            new_version=new_version,
            diff_text=diff_text,
            changed_sections=changed_sections,
            added_requirements=added_reqs,
            removed_requirements=removed_reqs,
            modified_requirements=[],  # Would need deeper analysis
            impact_score=impact_score,
        )

    def _extract_changed_sections(self, diff_text: str) -> list[str]:
        """Extract changed section names from diff."""
        sections = []
        # Look for markdown headers in diff context
        for line in diff_text.split("\n"):
            if line.startswith("+") or line.startswith("-"):
                match = re.match(r"^[+-]\s*#{1,6}\s+(.+)$", line)
                if match:
                    sections.append(match.group(1).strip())
        return list(set(sections))

    def _calculate_impact_score(self, diff_lines: int, total_lines: int, req_changes: int) -> float:
        """Calculate impact score 0-1."""
        if total_lines == 0:
            return 0.0

        # Base score on percentage of changed lines
        line_change_ratio = min(diff_lines / total_lines, 1.0)

        # Boost for requirement changes
        req_boost = min(req_changes * 0.1, 0.3)

        score = line_change_ratio * 0.7 + req_boost
        return round(min(score, 1.0), 2)

    def analyze_document(self, doc_path: str, content: str | None = None, force: bool = False) -> DocumentAnalysis:
        """Analyze document structure.

        Args:
            doc_path: Document path
            content: Document content (reads from file if None)
            force: Force re-analysis

        Returns:
            Document analysis
        """
        doc_name = os.path.basename(doc_path).replace(".", "_")

        # Check if already analyzed
        if not force:
            existing = self._load_analysis(doc_name)
            if existing:
                return existing

        if content is None:
            try:
                with open(doc_path, encoding="utf-8") as f:
                    content = f.read()
            except (RuntimeError, ValueError):
                content = ""

        # Parse requirements
        requirements = self._parse_requirements_inline(content, doc_path)

        # Parse interfaces
        interfaces = self._parse_interfaces(content, doc_path)

        # Detect dependencies
        dependencies = self._detect_dependencies(content)

        analysis = DocumentAnalysis(
            doc_path=doc_path,
            analyzed_at=_now_iso(),
            requirements=requirements,
            interfaces=interfaces,
            dependencies=dependencies,
            metadata={
                "line_count": len(content.splitlines()),
                "char_count": len(content),
                "checksum": self._compute_checksum(content),
            },
        )

        self._save_analysis(doc_name, analysis)

        # Update interfaces registry
        self._update_interfaces_registry(doc_path, interfaces)

        return analysis

    def _parse_requirements_inline(self, content: str, doc_path: str = "") -> list[ParsedRequirement]:
        """Parse requirements from document content."""
        requirements = []
        lines = content.split("\n")

        # Patterns for requirement detection
        patterns = [
            (r"#{1,6}\s*(.+?)(?:\n|$)", "heading"),
            (r"REQ:\s*(.+?)(?:\n|$)", "req_tag"),
            (r"【需求】\s*(.+?)(?:\n|$)", "cn_tag"),
        ]

        req_counter = 1
        for i, line in enumerate(lines):
            for pattern, _ptype in patterns:
                match = re.search(pattern, line)
                if match:
                    title = match.group(1).strip()
                    # Get context
                    context_start = max(0, i)
                    context_end = min(len(lines), i + 5)
                    description = "\n".join(lines[context_start:context_end]).strip()

                    req = ParsedRequirement(
                        id=f"REQ-PARSE-{req_counter:04d}",
                        title=title,
                        description=description,
                        section=f"line-{i + 1}",
                        line_start=i + 1,
                        line_end=context_end,
                    )
                    requirements.append(req)
                    req_counter += 1

        return requirements

    def _parse_interfaces(self, content: str, doc_path: str) -> list[ParsedInterface]:
        """Parse interface definitions from document."""
        interfaces = []

        # Patterns for interface detection
        # API endpoint pattern
        api_pattern = r"(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s\n]+)"
        # Function signature pattern
        func_pattern = r"def\s+(\w+)\s*\(([^)]*)\)"

        lines = content.split("\n")

        for i, line in enumerate(lines):
            # Match API endpoints
            api_match = re.search(api_pattern, line)
            if api_match:
                method, path = api_match.groups()
                iface = ParsedInterface(
                    name=f"{method} {path}",
                    interface_type="api",
                    signature=f"{method} {path}",
                    parameters=[],
                    returns=None,
                    description="",
                    section=f"line-{i + 1}",
                    line_start=i + 1,
                    line_end=i + 1,
                )
                interfaces.append(iface)

            # Match function definitions
            func_match = re.search(func_pattern, line)
            if func_match:
                name, params = func_match.groups()
                param_list = []
                if params:
                    for p in params.split(","):
                        p = p.strip()
                        if ":" in p:
                            pname, ptype = p.split(":", 1)
                            param_list.append({"name": pname.strip(), "type": ptype.strip()})
                        else:
                            param_list.append({"name": p, "type": "Any"})

                iface = ParsedInterface(
                    name=name,
                    interface_type="function",
                    signature=f"def {name}({params})",
                    parameters=param_list,
                    returns=None,
                    description="",
                    section=f"line-{i + 1}",
                    line_start=i + 1,
                    line_end=i + 1,
                )
                interfaces.append(iface)

        return interfaces

    def _detect_dependencies(self, content: str) -> list[str]:
        """Detect document dependencies."""
        deps = []

        # Pattern for document references
        patterns = [
            r"see\s+([^\n]+\.md)",
            r"参考\s+([^\n]+\.md)",
            r"@see\s+([^\n]+\.md)",
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                dep = match.group(1).strip()
                if dep not in deps:
                    deps.append(dep)

        return deps

    def _update_interfaces_registry(self, doc_path: str, interfaces: list[ParsedInterface]) -> None:
        """Update global interfaces registry."""
        registry_path = self.state_manager.get_data_path("documents", self.INTERFACES_FILE)

        registry = read_json_file(registry_path) or {"version": "1.0", "interfaces": {}}

        for iface in interfaces:
            registry["interfaces"][iface.name] = {
                "doc_path": doc_path,
                "type": iface.interface_type,
                "signature": iface.signature,
                "line": iface.line_start,
            }

        _write_json_atomic(registry_path, registry)

    def analyze_impact(self, doc_path: str, diff: DocumentDiff) -> dict[str, Any]:
        """Analyze the impact of document changes.

        Args:
            doc_path: Document path
            diff: Document diff

        Returns:
            Impact analysis
        """
        impact: dict[str, Any] = {
            "doc_path": doc_path,
            "impact_score": diff.impact_score,
            "affected_requirements": [],
            "affected_tasks": [],
            "affected_code": [],
            "recommendations": [],
        }

        # Find affected requirements
        from polaris.delivery.cli.pm.requirements_tracker import get_requirements_tracker

        try:
            tracker = get_requirements_tracker(self.workspace)
            requirements = tracker.get_requirements_by_source(doc_path)

            for req in requirements:
                # Check if requirement section was modified
                for section in diff.changed_sections:
                    if req.source_section and section in req.source_section:
                        impact["affected_requirements"].append(req.id)
                        impact["affected_tasks"].extend(req.tasks)
                        break
        except (RuntimeError, ValueError):
            logger.debug("DEBUG: document_manager.py:{626} {exc} (swallowed)")

        # Generate recommendations
        if diff.added_requirements:
            impact["recommendations"].append(f"新增 {len(diff.added_requirements)} 个需求，需要创建相应任务")

        if diff.removed_requirements:
            impact["recommendations"].append(f"移除 {len(diff.removed_requirements)} 个需求，需要清理相关任务")

        if diff.impact_score > 0.5:
            impact["recommendations"].append("文档变更影响较大，建议重新评估相关实现")

        return impact

    def auto_version_document(self, doc_path: str, content: str | None = None) -> DocumentVersion | None:
        """Automatically version document if changed.

        Args:
            doc_path: Document path
            content: Document content (reads from file if None)

        Returns:
            New version if created, None if unchanged
        """
        if content is None:
            try:
                with open(doc_path, encoding="utf-8") as f:
                    content = f.read()
            except (RuntimeError, ValueError):
                return None

        checksum = self._compute_checksum(content)

        # Check if changed
        versions = self._load_versions()
        doc_data = versions["documents"].get(doc_path, {})

        if doc_data.get("versions"):
            last_version = doc_data["versions"][-1]
            if last_version["checksum"] == checksum:
                return None  # No change

        # Create new version
        version_info = self.create_version(
            doc_path=doc_path,
            content=content,
            change_summary="Auto-versioned by DocumentManager",
        )

        # Re-analyze if changed
        self.analyze_document(doc_path, content, force=True)

        return version_info

    def get_all_analyzed_documents(self) -> list[str]:
        """Get list of all analyzed documents."""
        analysis_dir = self.state_manager.get_data_path("documents", self.ANALYSIS_DIR)
        if not os.path.exists(analysis_dir):
            return []

        docs = []
        for f in os.listdir(analysis_dir):
            if f.endswith(".json") and f != "interfaces.json":
                doc_name = f[:-5].replace("_", ".")
                docs.append(doc_name)

        return docs

    def export_document_report(self, output_path: str | None = None) -> str:
        """Export document analysis report."""
        versions = self._load_versions()

        documents_report: dict[str, dict[str, Any]] = {}
        report: dict[str, Any] = {
            "exported_at": _now_iso(),
            "total_documents": len(versions["documents"]),
            "documents": documents_report,
        }

        for doc_path, doc_data in versions["documents"].items():
            doc_name = os.path.basename(doc_path).replace(".", "_")
            analysis = self._load_analysis(doc_name)

            documents_report[doc_path] = {
                "version_count": len(doc_data.get("versions", [])),
                "current_version": doc_data.get("current"),
                "requirements_count": len(analysis.requirements) if analysis else 0,
                "interfaces_count": len(analysis.interfaces) if analysis else 0,
            }

        if output_path is None:
            from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

            output_path = os.path.join(self.workspace, get_workspace_metadata_dir_name(), "documents_report.json")

        _write_json_atomic(output_path, report)
        return output_path

    def list_documents(
        self,
        doc_type: str | None = None,
        pattern: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List all tracked documents in the workspace.

        Args:
            doc_type: Filter by document type (e.g., 'requirements', 'design', 'api')
            pattern: Glob pattern to filter paths
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            Dictionary with documents list and pagination info
        """
        versions = self._load_versions()
        documents = []

        for doc_path, doc_data in versions["documents"].items():
            # Apply pattern filter
            if pattern and not self._match_pattern(doc_path, pattern):
                continue

            # Apply type filter
            if doc_type:
                doc_name = os.path.basename(doc_path).lower()
                if doc_type.lower() not in doc_name:
                    continue

            versions_list = doc_data.get("versions", [])
            current_version = doc_data.get("current", "")

            # Get last modified time
            last_modified = versions_list[-1]["created_at"] if versions_list else ""

            documents.append(
                {
                    "path": doc_path,
                    "current_version": current_version,
                    "version_count": len(versions_list),
                    "last_modified": last_modified,
                    "created_at": versions_list[0]["created_at"] if versions_list else "",
                }
            )

        # Sort by last modified descending
        documents.sort(key=lambda x: x["last_modified"], reverse=True)

        total = len(documents)
        paginated = documents[offset : offset + limit]

        return {
            "documents": paginated,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    def _match_pattern(self, doc_path: str, pattern: str) -> bool:
        """Check if doc_path matches glob pattern."""
        import fnmatch

        return fnmatch.fnmatch(doc_path.lower(), pattern.lower())

    def update_document(
        self,
        doc_path: str,
        content: str,
        updated_by: str = "pm",
        change_summary: str = "",
    ) -> DocumentVersion | None:
        """Update document content and create new version.

        Args:
            doc_path: Document path
            content: New document content
            updated_by: Who made the update
            change_summary: Summary of changes

        Returns:
            New version info or None if update failed
        """
        # Check if document exists
        if not os.path.exists(doc_path):
            # Create new document
            try:
                os.makedirs(os.path.dirname(doc_path), exist_ok=True)
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except (RuntimeError, ValueError):
                return None
        else:
            # Update existing document
            try:
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except (RuntimeError, ValueError):
                return None

        # Create new version
        version_info = self.create_version(
            doc_path=doc_path,
            content=content,
            created_by=updated_by,
            change_summary=change_summary or "Updated via API",
        )

        # Re-analyze document
        self.analyze_document(doc_path, content, force=True)

        return version_info

    def delete_document(
        self,
        doc_path: str,
        delete_file: bool = True,
    ) -> bool:
        """Delete document and its version history.

        Args:
            doc_path: Document path
            delete_file: Whether to delete the actual file

        Returns:
            True if deleted successfully
        """
        try:
            versions = self._load_versions()

            if doc_path in versions["documents"]:
                # Remove from versions registry
                del versions["documents"][doc_path]
                self._save_versions(versions)

            # Delete analysis file
            doc_name = os.path.basename(doc_path).replace(".", "_")
            analysis_path = self.state_manager.get_data_path("documents", f"{self.ANALYSIS_DIR}/{doc_name}.json")
            if os.path.exists(analysis_path):
                os.remove(analysis_path)

            # Delete actual file if requested
            if delete_file and os.path.exists(doc_path):
                os.remove(doc_path)

            # Record to history
            self.state_manager.append_to_history(
                "documents",
                {
                    "action": "delete",
                    "doc_path": doc_path,
                    "deleted_at": _now_iso(),
                },
            )

            return True
        except (RuntimeError, ValueError):
            return False

    def search_documents(
        self,
        query: str,
        search_content: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search documents by content or path.

        Args:
            query: Search query string
            search_content: Whether to search in document content
            limit: Maximum results

        Returns:
            List of matching documents with relevance scores
        """
        versions = self._load_versions()
        results = []
        query_lower = query.lower()

        for doc_path, doc_data in versions["documents"].items():
            score = 0.0
            matches = []

            # Check path match
            if query_lower in doc_path.lower():
                score += 0.5
                matches.append("path")

            # Check content match
            if search_content:
                content = self.get_version_content(doc_path)
                if content and query_lower in content.lower():
                    score += 1.0
                    matches.append("content")

                    # Boost score for multiple occurrences
                    occurrences = content.lower().count(query_lower)
                    score += min(occurrences * 0.1, 0.5)

            if score > 0:
                results.append(
                    {
                        "path": doc_path,
                        "score": round(score, 2),
                        "matches": matches,
                        "current_version": doc_data.get("current", ""),
                        "version_count": len(doc_data.get("versions", [])),
                    }
                )

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def get_document_info(self, doc_path: str) -> dict[str, Any] | None:
        """Get full document information including versions and analysis.

        Args:
            doc_path: Document path

        Returns:
            Document info dictionary or None
        """
        versions = self._load_versions()
        doc_data = versions["documents"].get(doc_path)
        if not doc_data:
            return None

        doc_name = os.path.basename(doc_path).replace(".", "_")
        analysis = self._load_analysis(doc_name)

        return {
            "path": doc_path,
            "current_version": doc_data.get("current", ""),
            "versions": doc_data.get("versions", []),
            "analysis": {
                "requirements": [asdict(r) for r in analysis.requirements] if analysis else [],
                "interfaces": [asdict(i) for i in analysis.interfaces] if analysis else [],
                "dependencies": analysis.dependencies if analysis else [],
            }
            if analysis
            else None,
        }


# Global instance cache
_manager_instances: dict[str, DocumentManager] = {}


def get_document_manager(workspace: str) -> DocumentManager:
    """Get or create DocumentManager instance."""
    workspace_abs = os.path.abspath(workspace)
    if workspace_abs not in _manager_instances:
        _manager_instances[workspace_abs] = DocumentManager(workspace_abs)
    return _manager_instances[workspace_abs]


def reset_document_manager(workspace: str) -> None:
    """Reset manager instance for workspace."""
    workspace_abs = os.path.abspath(workspace)
    _manager_instances.pop(workspace_abs, None)
