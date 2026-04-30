"""Tests for polaris.delivery.cli.visualization.keyboard."""

from __future__ import annotations

from polaris.delivery.cli.visualization.keyboard import (
    TERMINAL_CONTROL_CHARS,
    ANSIEscapeSeq,
    FoldShortcut,
    KeyboardShortcutConfig,
    parse_escape_sequence,
    validate_shortcut,
)


class TestFoldShortcut:
    def test_expand_all_debug(self) -> None:
        assert FoldShortcut.EXPAND_ALL_DEBUG.value == "alt+d"

    def test_collapse_all_debug(self) -> None:
        assert FoldShortcut.COLLAPSE_ALL_DEBUG.value == "alt+shift+d"

    def test_toggle_current(self) -> None:
        assert FoldShortcut.TOGGLE_CURRENT.value == "space"

    def test_expand_all(self) -> None:
        assert FoldShortcut.EXPAND_ALL.value == "ctrl+alt+["

    def test_fold_to_level_1(self) -> None:
        assert FoldShortcut.FOLD_TO_LEVEL_1.value == "1"

    def test_search(self) -> None:
        assert FoldShortcut.SEARCH.value == "ctrl+f"


class TestKeyboardShortcutConfig:
    def test_defaults(self) -> None:
        config = KeyboardShortcutConfig.default()
        assert config.enabled is True
        assert config.alt_enabled is True
        assert config.ctrl_alt_enabled is True
        assert config.custom_shortcuts is None

    def test_minimal(self) -> None:
        config = KeyboardShortcutConfig.minimal()
        assert config.alt_enabled is False
        assert config.ctrl_alt_enabled is False

    def test_get_shortcut_default(self) -> None:
        config = KeyboardShortcutConfig.default()
        assert config.get_shortcut(FoldShortcut.EXPAND_ALL_DEBUG) == "alt+d"

    def test_get_shortcut_custom(self) -> None:
        config = KeyboardShortcutConfig(custom_shortcuts={FoldShortcut.EXPAND_ALL_DEBUG: "ctrl+d"})
        assert config.get_shortcut(FoldShortcut.EXPAND_ALL_DEBUG) == "ctrl+d"

    def test_is_available_alt_enabled(self) -> None:
        config = KeyboardShortcutConfig(alt_enabled=True)
        assert config.is_available(FoldShortcut.EXPAND_ALL_DEBUG) is True

    def test_is_available_alt_disabled(self) -> None:
        config = KeyboardShortcutConfig(alt_enabled=False)
        assert config.is_available(FoldShortcut.EXPAND_ALL_DEBUG) is False

    def test_is_available_ctrl_alt_disabled(self) -> None:
        config = KeyboardShortcutConfig(ctrl_alt_enabled=False)
        assert config.is_available(FoldShortcut.EXPAND_ALL) is False

    def test_is_available_no_modifier(self) -> None:
        config = KeyboardShortcutConfig(alt_enabled=False, ctrl_alt_enabled=False)
        assert config.is_available(FoldShortcut.TOGGLE_CURRENT) is True
        assert config.is_available(FoldShortcut.FOLD_TO_LEVEL_1) is True


class TestANSIEscapeSeq:
    def test_constants(self) -> None:
        assert ANSIEscapeSeq.ALT_PREFIX == "\x1b["
        assert ANSIEscapeSeq.SPACE == " "
        assert ANSIEscapeSeq.ENTER == "\n"
        assert ANSIEscapeSeq.ESCAPE == "\x1b"


class TestParseEscapeSequence:
    def test_alt_d(self) -> None:
        result = parse_escape_sequence(ANSIEscapeSeq.ALT_D)
        assert result == FoldShortcut.EXPAND_ALL_DEBUG

    def test_alt_t(self) -> None:
        result = parse_escape_sequence(ANSIEscapeSeq.ALT_T)
        assert result == FoldShortcut.EXPAND_ALL_THINKING

    def test_alt_o(self) -> None:
        result = parse_escape_sequence(ANSIEscapeSeq.ALT_O)
        assert result == FoldShortcut.EXPAND_ALL_TOOL

    def test_unknown(self) -> None:
        result = parse_escape_sequence("\x1b[x")
        assert result is None

    def test_empty(self) -> None:
        result = parse_escape_sequence("")
        assert result is None


class TestTerminalControlChars:
    def test_has_ctrl_c(self) -> None:
        assert "\x03" in TERMINAL_CONTROL_CHARS
        assert "Ctrl+C" in TERMINAL_CONTROL_CHARS["\x03"]

    def test_has_ctrl_d(self) -> None:
        assert "\x04" in TERMINAL_CONTROL_CHARS

    def test_has_ctrl_z(self) -> None:
        assert "\x1a" in TERMINAL_CONTROL_CHARS


class TestValidateShortcut:
    def test_safe_shortcut(self) -> None:
        safe, conflict = validate_shortcut("alt+d")
        assert safe is True
        assert conflict is None

    def test_ctrl_c_conflict(self) -> None:
        safe, conflict = validate_shortcut("\x03")
        assert safe is False
        assert conflict is not None

    def test_ctrl_d_conflict(self) -> None:
        safe, conflict = validate_shortcut("\x04")
        assert safe is False
        assert "Ctrl+D" in conflict

    def test_no_conflict(self) -> None:
        safe, conflict = validate_shortcut("space")
        assert safe is True
        assert conflict is None
