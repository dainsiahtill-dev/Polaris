from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedOperation:
    kind: str  # search_replace | full_file | create | delete
    path: str
    search: str = ""
    replace: str = ""
    content: str = ""
    move_to: str = ""


def _hunk_to_before_after(lines: list[str]) -> tuple[str, str]:
    before: list[str] = []
    after: list[str] = []
    for line in lines:
        if line.startswith("@@") or line.strip() == "*** End of File":
            continue

        if len(line) < 2:
            before.append(line)
            after.append(line)
            continue

        op = line[0]
        payload = line[1:]
        if op == " ":
            before.append(payload)
            after.append(payload)
        elif op == "-":
            before.append(payload)
        elif op == "+":
            after.append(payload)

    return "".join(before), "".join(after)


def _split_update_hunks(lines: list[str]) -> list[list[str]]:
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        hunks.append(current)
    return hunks


def extract_apply_patch_operations(text: str) -> list[RoutedOperation]:
    """Parse apply_patch-like text into normalized routed operations."""
    if "*** Begin Patch" not in text and "*** Add File:" not in text and "*** Update File:" not in text:
        return []

    lines = text.splitlines(keepends=True)
    idx = 0
    if idx < len(lines) and lines[idx].strip() == "*** Begin Patch":
        idx += 1

    ops: list[RoutedOperation] = []
    while idx < len(lines):
        raw = lines[idx].strip()
        if raw == "*** End Patch":
            break

        if raw.startswith("*** Add File: "):
            path = raw[len("*** Add File: ") :].strip()
            idx += 1
            body: list[str] = []
            while idx < len(lines) and not lines[idx].startswith("*** "):
                line = lines[idx]
                if line.startswith("+"):
                    body.append(line[1:])
                elif not line.strip():
                    body.append("\n")
                idx += 1
            ops.append(RoutedOperation(kind="create", path=path, content="".join(body)))
            continue

        if raw.startswith("*** Delete File: "):
            path = raw[len("*** Delete File: ") :].strip()
            idx += 1
            ops.append(RoutedOperation(kind="delete", path=path))
            continue

        if raw.startswith("*** Update File: "):
            path = raw[len("*** Update File: ") :].strip()
            idx += 1
            move_to = ""
            if idx < len(lines) and lines[idx].strip().startswith("*** Move to: "):
                move_to = lines[idx].strip()[len("*** Move to: ") :].strip()
                idx += 1

            block: list[str] = []
            while idx < len(lines) and not lines[idx].startswith("*** "):
                block.append(lines[idx])
                idx += 1

            hunks = _split_update_hunks(block)
            if not hunks:
                before, after = _hunk_to_before_after(block)
                if before or after:
                    ops.append(
                        RoutedOperation(
                            kind="search_replace",
                            path=path,
                            search=before,
                            replace=after,
                            move_to=move_to,
                        )
                    )
                continue

            for hunk in hunks:
                before, after = _hunk_to_before_after(hunk)
                if before or after:
                    ops.append(
                        RoutedOperation(
                            kind="search_replace",
                            path=path,
                            search=before,
                            replace=after,
                            move_to=move_to,
                        )
                    )
            continue

        idx += 1

    return ops
