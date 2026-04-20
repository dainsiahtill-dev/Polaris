from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from ..storage.index_cache import load_index_rows
from ..utils import normalize_path_str as _normalize_path

if TYPE_CHECKING:
    from pathlib import Path


def get_inheritance_tree(
    index_dir: Path,
    class_name: str = "",
    include_interfaces: bool = True,
) -> dict[str, Any]:
    """Get class inheritance relationships.

    Args:
        index_dir: Path to the index directory.
        class_name: Optional class name to filter (empty = all classes).
        include_interfaces: Whether to include interface/protocol classes.

    Returns:
        Dict with inheritance tree structure.
    """
    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    classes: list[dict[str, Any]] = []
    class_name_filter = str(class_name or "").strip().lower()

    for row in all_symbols:
        kind = str(row.get("kind", "")).strip().lower()
        if kind != "class":
            continue

        symbol = str(row.get("symbol", "")).strip()
        qualified = str(row.get("qualified_name", "")).strip()

        if class_name_filter and (
            symbol.lower() != class_name_filter
            and qualified.lower() != class_name_filter
            and not qualified.lower().endswith("." + class_name_filter)
        ):
            continue

        bases = row.get("bases", [])
        if not isinstance(bases, list):
            bases = []

        is_interface = _is_interface_class(row)
        if not include_interfaces and is_interface:
            continue

        classes.append(
            {
                "symbol": symbol,
                "qualified_name": qualified,
                "file": _normalize_path(str(row.get("file", ""))),
                "line_start": int(row.get("line_start", 1)),
                "bases": bases,
                "is_interface": is_interface,
            }
        )

    child_map: dict[str, list[str]] = defaultdict(list)
    for cls in classes:
        for base in cls["bases"]:
            base_name = str(base).strip()
            if base_name:
                child_map[base_name.lower()].append(cls["qualified_name"])

    roots: list[dict[str, Any]] = []
    non_roots: set[str] = set()

    for cls in classes:
        for base in cls["bases"]:
            base_lower = str(base).strip().lower()
            for other in classes:
                if other["symbol"].lower() == base_lower or other["qualified_name"].lower() == base_lower:
                    non_roots.add(cls["qualified_name"].lower())
                    break

    for cls in classes:
        if cls["qualified_name"].lower() not in non_roots:
            roots.append(cls)

    def _build_subtree(cls: dict[str, Any], visited: set[str]) -> dict[str, Any]:
        qname = cls["qualified_name"]
        if qname.lower() in visited:
            return {
                "symbol": cls["symbol"],
                "qualified_name": qname,
                "file": cls["file"],
                "line_start": cls["line_start"],
                "is_interface": cls.get("is_interface", False),
                "children": [],
                "circular_ref": True,
            }
        visited.add(qname.lower())

        children: list[dict[str, Any]] = []
        for child_qname in child_map.get(cls["symbol"].lower(), []):
            for other in classes:
                if other["qualified_name"].lower() == child_qname.lower():
                    children.append(_build_subtree(other, visited.copy()))
                    break
        for child_qname in child_map.get(qname.lower(), []):
            if any(c["qualified_name"].lower() == child_qname.lower() for c in children):
                continue
            for other in classes:
                if other["qualified_name"].lower() == child_qname.lower():
                    children.append(_build_subtree(other, visited.copy()))
                    break

        return {
            "symbol": cls["symbol"],
            "qualified_name": qname,
            "file": cls["file"],
            "line_start": cls["line_start"],
            "bases": cls["bases"],
            "is_interface": cls.get("is_interface", False),
            "children": children,
        }

    tree: list[dict[str, Any]] = []
    for root in roots:
        tree.append(_build_subtree(root, set()))

    return {
        "class_count": len(classes),
        "root_count": len(roots),
        "filter": class_name_filter or None,
        "include_interfaces": include_interfaces,
        "tree": tree,
        "flat_classes": classes,
    }


def _is_interface_class(row: dict[str, Any]) -> bool:
    """Heuristic to detect if a class is an interface/protocol."""
    symbol = str(row.get("symbol", "")).strip().lower()
    bases = row.get("bases", [])

    if symbol.startswith("i") and len(symbol) > 1 and symbol[1].isupper():
        return True

    interface_keywords = {"protocol", "interface", "abc", "abstract"}
    if any(kw in symbol for kw in interface_keywords):
        return True

    if isinstance(bases, list):
        for base in bases:
            base_lower = str(base).strip().lower()
            if any(kw in base_lower for kw in ("protocol", "abc", "interface")):
                return True

    decorators = row.get("decorators", [])
    if isinstance(decorators, list):
        for dec in decorators:
            if "abstract" in str(dec).lower():
                return True

    return False


def get_file_dependencies(
    index_dir: Path,
    file_path: str = "",
    direction: str = "both",
) -> dict[str, Any]:
    """Get file dependency relationships.

    Args:
        index_dir: Path to the index directory.
        file_path: Optional file path to filter.
        direction: Direction (imports/exported/both).

    Returns:
        Dict with dependency information.
    """
    all_deps = load_index_rows(index_dir, kind="dependencies", key_field="file")

    direction = str(direction or "both").strip().lower()
    if direction not in {"imports", "exported", "both"}:
        direction = "both"

    file_filter = _normalize_path(file_path).lower() if file_path else ""

    imports_map: dict[str, set[str]] = defaultdict(set)
    for dep in all_deps:
        source = _normalize_path(str(dep.get("file", "")))
        target = _normalize_path(str(dep.get("edge_to", "")))
        if source and target:
            imports_map[source.lower()].add(target)

    exported_map: dict[str, set[str]] = defaultdict(set)
    for source, targets in imports_map.items():
        for target in targets:
            exported_map[target.lower()].add(source)

    if file_filter:
        imports_list: list[str] = []
        exported_list: list[str] = []

        if direction in {"imports", "both"}:
            imports_list = sorted(imports_map.get(file_filter, set()))

        if direction in {"exported", "both"}:
            exported_list = sorted(exported_map.get(file_filter, set()))

        return {
            "file": file_path,
            "direction": direction,
            "imports": imports_list,
            "imports_count": len(imports_list),
            "exported_to": exported_list,
            "exported_count": len(exported_list),
        }

    files: list[dict[str, Any]] = []
    all_files = set(imports_map.keys()) | set(exported_map.keys())

    for f in sorted(all_files):
        entry: dict[str, Any] = {"file": f}
        if direction in {"imports", "both"}:
            entry["imports"] = sorted(imports_map.get(f, set()))
            entry["imports_count"] = len(entry["imports"])
        if direction in {"exported", "both"}:
            entry["exported_to"] = sorted(exported_map.get(f, set()))
            entry["exported_count"] = len(entry["exported_to"])
        files.append(entry)

    return {
        "direction": direction,
        "file_count": len(files),
        "total_edges": sum(len(targets) for targets in imports_map.values()),
        "files": files[:100],
        "truncated": len(files) > 100,
    }
