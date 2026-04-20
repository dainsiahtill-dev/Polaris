from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.kernelone.editing.editblock_engine import extract_edit_blocks
from polaris.kernelone.editing.patch_engine import RoutedOperation, extract_apply_patch_operations
from polaris.kernelone.editing.unified_diff_engine import extract_unified_diff_edits
from polaris.kernelone.editing.wholefile_engine import extract_wholefile_blocks

if TYPE_CHECKING:
    from collections.abc import Sequence


def route_edit_operations(
    text: str,
    *,
    inchat_files: Sequence[str],
) -> list[RoutedOperation]:
    """Route rich editing formats into normalized operations.

    Priority:
    1) apply_patch format
    2) SEARCH/REPLACE edit blocks
    3) unified diff fences
    4) whole-file fenced blocks
    """
    patch_ops = extract_apply_patch_operations(text)
    if patch_ops:
        return patch_ops

    edit_blocks = extract_edit_blocks(text, valid_filenames=list(inchat_files))
    if edit_blocks:
        ops: list[RoutedOperation] = []
        for p, s, r in edit_blocks:
            # Ensure search and replace are strings (not list)
            search_text: str = s if isinstance(s, str) else "".join(s) if isinstance(s, list) else str(s)
            replace_text: str = r if isinstance(r, str) else "".join(r) if isinstance(r, list) else str(r)
            ops.append(RoutedOperation(kind="search_replace", path=p, search=search_text, replace=replace_text))  # type: ignore[arg-type]
        return ops

    udiff = extract_unified_diff_edits(text)
    if udiff:
        ops_udiff: list[RoutedOperation] = []
        for p, before, after in udiff:
            if p:
                # Ensure before and after are strings (not list)
                before_text: str = before if isinstance(before, str) else "".join(before)
                after_text: str = after if isinstance(after, str) else "".join(after)
                ops_udiff.append(RoutedOperation(kind="search_replace", path=p, search=before_text, replace=after_text))
        return ops_udiff

    whole = extract_wholefile_blocks(text, inchat_files=list(inchat_files))
    if whole:
        return [RoutedOperation(kind="full_file", path=p, content=body) for p, body in whole]

    return []
