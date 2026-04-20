from __future__ import annotations

from typing import Any


def _is_comment_line(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return stripped.startswith(("#", "//", "/*", "*", "*/", '"""', "'''"))


def _is_import_line(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return stripped.startswith(("import ", "from ")) and "(" not in stripped and ")" not in stripped


def _line_has_any_token(text: str, task_tokens: list[str]) -> bool:
    if not task_tokens:
        return False
    low = str(text or "").lower()
    return any(token in low for token in task_tokens)


def compress_snippet_content(
    content: str,
    *,
    max_chars: int,
    task_tokens: list[str] | None = None,
    symbol: str = "",
    enable_rules: bool = True,
) -> tuple[str, dict[str, Any]]:
    original = str(content or "")
    rules = {
        "trim_import_block": 0,
        "trim_comment_block": 0,
        "collapse_blank_runs": 0,
        "drop_low_signal": 0,
        "truncate_max_chars": 0,
    }
    if not enable_rules:
        compact = original[:max_chars] if len(original) > max_chars else original
        return compact, {
            "rules": rules,
            "original_chars": len(original),
            "compressed_chars": len(compact),
            "saved_chars": max(0, len(original) - len(compact)),
            "dropped": False,
        }

    token_list = [str(item).strip().lower() for item in (task_tokens or []) if str(item).strip()]
    original_low = original.lower()
    has_symbol = bool(str(symbol or "").strip())
    if (
        not has_symbol
        and token_list
        and len(original) > 1200
        and not any(token in original_low for token in token_list)
    ):
        rules["drop_low_signal"] += 1
        return "", {
            "rules": rules,
            "original_chars": len(original),
            "compressed_chars": 0,
            "saved_chars": len(original),
            "dropped": True,
            "coverage": 0.0,
        }

    lines = [line.rstrip() for line in original.splitlines()]

    compact_lines: list[str] = []
    blank_run = 0
    import_run = 0
    comment_run = 0
    suppressed_imports = 0
    suppressed_comments = 0
    matched_token_lines = 0
    total_nonempty_lines = 0

    for line in lines:
        stripped = line.strip()
        if stripped:
            total_nonempty_lines += 1
            if _line_has_any_token(line, token_list):
                matched_token_lines += 1

        if _is_import_line(line):
            import_run += 1
            if import_run > 12:
                suppressed_imports += 1
                rules["trim_import_block"] += 1
                continue
        else:
            import_run = 0

        if _is_comment_line(line):
            comment_run += 1
            if comment_run > 8:
                suppressed_comments += 1
                rules["trim_comment_block"] += 1
                continue
        else:
            comment_run = 0

        if not stripped:
            blank_run += 1
            if blank_run > 1:
                rules["collapse_blank_runs"] += 1
                continue
            compact_lines.append("")
            continue

        blank_run = 0
        compact_lines.append(line)

    if suppressed_imports > 0:
        compact_lines.append(f"# ... [omitted {suppressed_imports} import lines]")
    if suppressed_comments > 0:
        compact_lines.append(f"# ... [omitted {suppressed_comments} comment lines]")

    compact = "\n".join(compact_lines).strip("\n")

    coverage = (float(matched_token_lines) / float(total_nonempty_lines)) if total_nonempty_lines > 0 else 0.0
    if not has_symbol and coverage <= 0.0 and len(compact) > 1200:
        rules["drop_low_signal"] += 1
        return "", {
            "rules": rules,
            "original_chars": len(original),
            "compressed_chars": 0,
            "saved_chars": len(original),
            "dropped": True,
            "coverage": round(coverage, 6),
        }

    if len(compact) > max_chars:
        marker = "\n... [truncated]"
        keep = max(0, int(max_chars) - len(marker))
        compact = compact[:keep].rstrip() + marker
        rules["truncate_max_chars"] += 1

    if not compact:
        compact = original[: max(0, int(max_chars))]

    return compact, {
        "rules": rules,
        "original_chars": len(original),
        "compressed_chars": len(compact),
        "saved_chars": max(0, len(original) - len(compact)),
        "dropped": False,
        "coverage": round(coverage, 6),
    }
