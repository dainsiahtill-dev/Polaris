from __future__ import annotations

import difflib


def create_progress_bar(percentage: float) -> str:
    block = "#"
    empty = "-"
    total = 30
    filled = int(total * percentage // 100)
    return block * filled + empty * (total - filled)


def _assert_newline_contract(lines: list[str]) -> None:
    if not lines:
        return
    for line in lines[:-1]:
        if line and not line.endswith("\n"):
            raise ValueError(f"Expected newline-terminated line, got: {line!r}")


def find_last_non_deleted(lines_orig: list[str], lines_updated: list[str]) -> int | None:
    diff = list(difflib.ndiff(lines_orig, lines_updated))
    num_orig = 0
    last_non_deleted_orig = None

    for line in diff:
        code = line[:1]
        if code == " ":
            num_orig += 1
            last_non_deleted_orig = num_orig
        elif code == "-":
            num_orig += 1

    return last_non_deleted_orig


def diff_partial_update(
    lines_orig: list[str],
    lines_updated: list[str],
    *,
    final: bool = False,
    file_name: str | None = None,
) -> str:
    """Render a stable unified diff for incremental whole-file updates."""
    _assert_newline_contract(lines_orig)
    num_orig = len(lines_orig)

    if final:
        last_non_deleted = num_orig
    else:
        _last = find_last_non_deleted(lines_orig, lines_updated)
        if _last is None:
            return ""
        last_non_deleted = _last

    pct = (last_non_deleted * 100 / num_orig) if num_orig else 50
    bar = create_progress_bar(pct)
    status = f" {last_non_deleted:3d} / {num_orig:3d} lines [{bar}] {pct:3.0f}%\n"

    scoped_orig = lines_orig[:last_non_deleted]
    scoped_updated = lines_updated
    if not final and scoped_updated:
        scoped_updated = [*scoped_updated[:-1], status]

    diff = list(difflib.unified_diff(scoped_orig, scoped_updated, n=5))[2:]
    payload = "".join(diff)
    if payload and not payload.endswith("\n"):
        payload += "\n"

    backticks = "```"
    while backticks in payload:
        backticks += "`"

    header = f"{backticks}diff\n"
    if file_name:
        header += f"--- {file_name} original\n+++ {file_name} updated\n"
    return f"{header}{payload}{backticks}\n\n"
