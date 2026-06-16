"""F23 (2026-06-16): write_file edit-fragment guard.

Weak models sometimes emit a line-anchored insertion directive as the ENTIRE
write_file content (treating a full-file write like an incremental edit),
producing a broken stub. ``is_edit_fragment_write_violation`` catches the
unambiguous case while leaving real files alone.
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
    is_edit_fragment_write_violation,
)


class TestEditFragmentWriteGuard:
    def test_catches_canonical_l2_08_fragment(self) -> None:
        # The exact 28-byte stub the L2-08 Director emitted.
        assert is_edit_fragment_write_violation("app.js", "// 第 70 行之后添加\n}") is True

    def test_catches_english_insert_after_line(self) -> None:
        assert is_edit_fragment_write_violation("src/app.jsx", "// insert after line 42\nreturn x;") is True

    def test_catches_zh_modify_line(self) -> None:
        assert is_edit_fragment_write_violation("main.py", "在第 12 行添加: print('x')") is True

    # --- negatives: must NOT flag real files ---

    def test_real_complete_js_file_not_flagged(self) -> None:
        content = (
            "function tick() {\n"
            "  const now = new Date();\n"
            "  document.getElementById('clock').textContent = now.toLocaleTimeString();\n"
            "}\n"
            "setInterval(tick, 1000);\n"
        )
        assert is_edit_fragment_write_violation("app.js", content) is False

    def test_short_real_file_without_directive_not_flagged(self) -> None:
        assert is_edit_fragment_write_violation("config.js", "export const PORT = 3000;\n") is False

    def test_comment_mentioning_line_count_not_flagged(self) -> None:
        # A legitimate comment that mentions a line but is not an insertion directive.
        assert is_edit_fragment_write_violation("util.js", "// this helper is line-oriented\nfunction f(){}\n") is False

    def test_long_content_never_flagged(self) -> None:
        # Even with a stray 'line 5' mention, a substantial file is never a fragment.
        big = "// add after line 5 historically\n" + ("const x = 1;\n" * 60)
        assert is_edit_fragment_write_violation("app.js", big) is False

    def test_non_code_extension_not_flagged(self) -> None:
        # README/text targets are out of scope for this code-fragment guard.
        assert is_edit_fragment_write_violation("notes.txt", "在第 3 行添加一句话") is False

    def test_empty_not_flagged(self) -> None:
        assert is_edit_fragment_write_violation("app.js", "") is False
        assert is_edit_fragment_write_violation("app.js", "   ") is False
