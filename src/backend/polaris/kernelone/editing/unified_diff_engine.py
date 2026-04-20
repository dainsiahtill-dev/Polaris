from __future__ import annotations


def hunk_to_before_after(hunk: list[str], *, as_lines: bool = False) -> tuple[str, str] | tuple[list[str], list[str]]:
    before: list[str] = []
    after: list[str] = []
    for line in hunk:
        if len(line) < 2:
            op = " "
            payload = line
        else:
            op = line[0]
            payload = line[1:]

        if op == " ":
            before.append(payload)
            after.append(payload)
        elif op == "-":
            before.append(payload)
        elif op == "+":
            after.append(payload)

    if as_lines:
        return before, after
    return "".join(before), "".join(after)


def _process_fenced_block(lines: list[str], start: int) -> tuple[int, list[tuple[str, list[str]]]]:
    end = start
    while end < len(lines) and not lines[end].startswith("```"):
        end += 1

    block = lines[start:end]
    block.append("@@ @@")

    if len(block) >= 2 and block[0].startswith("--- ") and block[1].startswith("+++ "):
        a_name = block[0][4:].strip()
        b_name = block[1][4:].strip()
        if (a_name.startswith("a/") or a_name == "/dev/null") and b_name.startswith("b/"):
            current_file = b_name[2:]
        else:
            current_file = b_name
        block = block[2:]
    else:
        current_file = ""

    edits: list[tuple[str, list[str]]] = []
    keeper = False
    hunk: list[str] = []
    for line in block:
        hunk.append(line)
        if len(line) < 2:
            continue

        if line.startswith("+++ ") and len(hunk) >= 2 and hunk[-2].startswith("--- "):
            hunk = hunk[:-2]
            if current_file and hunk:
                edits.append((current_file, hunk))
            hunk = []
            keeper = False
            current_file = line[4:].strip()
            continue

        op = line[0]
        if op in "-+":
            keeper = True
            continue
        if op != "@":
            continue
        if not keeper:
            hunk = []
            continue

        hunk = hunk[:-1]
        if current_file and hunk:
            edits.append((current_file, hunk))
        hunk = []
        keeper = False

    return end + 1, edits


def extract_unified_diff_edits(content: str) -> list[tuple[str, str | list[str], str | list[str]]]:
    """Extract (path, before, after) edits from fenced ```diff blocks."""
    if not content.strip():
        return []

    if not content.endswith("\n"):
        content += "\n"

    lines = content.splitlines(keepends=True)
    idx = 0
    edits: list[tuple[str, str | list[str], str | list[str]]] = []
    while idx < len(lines):
        if lines[idx].startswith("```diff"):
            idx, parsed = _process_fenced_block(lines, idx + 1)
            for path, hunk in parsed:
                before, after = hunk_to_before_after(hunk)
                if before or after:
                    edits.append((path, before, after))
            continue
        idx += 1

    return edits
