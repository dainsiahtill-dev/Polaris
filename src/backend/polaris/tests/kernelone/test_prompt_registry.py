"""Tests for polaris.kernelone.prompt_registry.

Pure function tests for PromptTemplate dataclass and PromptRegistry methods.
No filesystem I/O mocking required for the core logic paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.prompt_registry import PromptRegistry, PromptTemplate

# =============================================================================
# PromptTemplate dataclass
# =============================================================================


def test_prompt_template_immutable() -> None:
    template = PromptTemplate(name="greet", content="Hello {{ name }}", required_variables=("name",))
    with pytest.raises(AttributeError):
        template.name = "other"  # type: ignore[misc]


def test_prompt_template_equality() -> None:
    t1 = PromptTemplate(name="a", content="b", required_variables=("c",))
    t2 = PromptTemplate(name="a", content="b", required_variables=("c",))
    t3 = PromptTemplate(name="x", content="b", required_variables=("c",))
    assert t1 == t2
    assert t1 != t3
    assert hash(t1) == hash(t2)


def test_prompt_template_repr() -> None:
    template = PromptTemplate(name="test", content="content", required_variables=())
    assert "PromptTemplate" in repr(template)
    assert "test" in repr(template)


# =============================================================================
# PromptRegistry.register
# =============================================================================


def test_register_basic_template() -> None:
    registry = PromptRegistry()
    result = registry.register("hello", "Hello {{ name }}")
    assert result.name == "hello"
    assert result.content == "Hello {{ name }}"
    assert result.required_variables == ("name",)


def test_register_no_variables() -> None:
    registry = PromptRegistry()
    result = registry.register("static", "Hello world")
    assert result.required_variables == ()


def test_register_multiple_variables_sorted() -> None:
    registry = PromptRegistry()
    result = registry.register("multi", "{{ b }} {{ a }} {{ c }}")
    assert result.required_variables == ("a", "b", "c")


def test_register_duplicate_variables_deduped() -> None:
    registry = PromptRegistry()
    result = registry.register("dup", "{{ x }} {{ x }} {{ y }}")
    assert result.required_variables == ("x", "y")


def test_register_overwrites_existing() -> None:
    registry = PromptRegistry()
    registry.register("same", "first")
    second = registry.register("same", "second")
    assert second.content == "second"
    assert registry.get("same").content == "second"


@pytest.mark.parametrize(
    "name",
    ["", "   ", None],
)
def test_register_rejects_empty_name(name: Any) -> None:
    registry = PromptRegistry()
    with pytest.raises(ValueError, match="template name cannot be empty"):
        registry.register(name, "content")


def test_register_strips_whitespace_from_name() -> None:
    registry = PromptRegistry()
    result = registry.register("  spaced  ", "content")
    assert result.name == "spaced"
    assert registry.get("spaced").content == "content"


def test_register_returns_prompt_template_instance() -> None:
    registry = PromptRegistry()
    result = registry.register("t", "{{ v }}")
    assert isinstance(result, PromptTemplate)


# =============================================================================
# PromptRegistry.get
# =============================================================================


def test_get_existing_template() -> None:
    registry = PromptRegistry()
    registry.register("found", "content")
    assert registry.get("found").name == "found"


def test_get_missing_raises_keyerror() -> None:
    registry = PromptRegistry()
    with pytest.raises(KeyError, match="template not found: missing"):
        registry.get("missing")


def test_get_is_case_sensitive() -> None:
    registry = PromptRegistry()
    registry.register("Case", "content")
    with pytest.raises(KeyError):
        registry.get("case")


# =============================================================================
# PromptRegistry.render
# =============================================================================


def test_render_basic() -> None:
    registry = PromptRegistry()
    registry.register("greet", "Hello {{ name }}")
    assert registry.render("greet", {"name": "Alice"}) == "Hello Alice"


def test_render_multiple_variables() -> None:
    registry = PromptRegistry()
    registry.register("multi", "{{ a }} + {{ b }} = {{ c }}")
    assert registry.render("multi", {"a": "1", "b": "2", "c": "3"}) == "1 + 2 = 3"


def test_render_extra_variables_allowed() -> None:
    registry = PromptRegistry()
    registry.register("greet", "Hello {{ name }}")
    result = registry.render("greet", {"name": "Bob", "extra": "ignored"})
    assert result == "Hello Bob"


def test_render_missing_variable_raises() -> None:
    registry = PromptRegistry()
    registry.register("greet", "Hello {{ name }}")
    with pytest.raises(ValueError, match="missing template variables: name"):
        registry.render("greet", {})


def test_render_multiple_missing_variables_sorted_message() -> None:
    registry = PromptRegistry()
    registry.register("multi", "{{ b }} {{ a }}")
    with pytest.raises(ValueError, match="missing template variables: a, b"):
        registry.render("multi", {})


def test_render_no_variables() -> None:
    registry = PromptRegistry()
    registry.register("static", "Hello world")
    assert registry.render("static", {}) == "Hello world"
    assert registry.render("static", {"extra": "ok"}) == "Hello world"


def test_render_unknown_template_raises() -> None:
    registry = PromptRegistry()
    with pytest.raises(KeyError, match="template not found: unknown"):
        registry.render("unknown", {})


def test_render_with_jinja2_filters() -> None:
    registry = PromptRegistry()
    registry.register("filtered", "{{ name|upper }}")
    assert registry.render("filtered", {"name": "alice"}) == "ALICE"


def test_render_undefined_variable_raises_strict() -> None:
    registry = PromptRegistry()
    registry.register("strict", "{{ maybe }}")
    with pytest.raises(ValueError, match="missing template variables: maybe"):
        registry.render("strict", {})


# =============================================================================
# PromptRegistry.list_templates
# =============================================================================


def test_list_templates_empty() -> None:
    registry = PromptRegistry()
    assert registry.list_templates() == ()


def test_list_templates_sorted() -> None:
    registry = PromptRegistry()
    registry.register("zebra", "z")
    registry.register("apple", "a")
    registry.register("mango", "m")
    assert registry.list_templates() == ("apple", "mango", "zebra")


def test_list_templates_returns_tuple() -> None:
    registry = PromptRegistry()
    registry.register("t", "c")
    result = registry.list_templates()
    assert isinstance(result, tuple)


# =============================================================================
# PromptRegistry.load_yaml_file (pure logic paths)
# =============================================================================


def test_load_yaml_file_dict_with_template_key(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "prompts.yaml"
    yaml_path.write_text("greet:\n  template: Hello {{ name }}\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 1
    assert registry.get("greet").content == "Hello {{ name }}"


def test_load_yaml_file_dict_with_content_key(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "prompts.yaml"
    yaml_path.write_text("farewell:\n  content: Goodbye {{ name }}\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 1
    assert registry.get("farewell").content == "Goodbye {{ name }}"


def test_load_yaml_file_flat_dict(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "prompts.yaml"
    yaml_path.write_text("static: Hello world\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 1
    assert registry.get("static").content == "Hello world"


def test_load_yaml_file_empty_file(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "empty.yaml"
    yaml_path.write_text("", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 0
    assert registry.list_templates() == ()


def test_load_yaml_file_non_dict_top_level(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "list.yaml"
    yaml_path.write_text("- item1\n- item2\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 0


def test_load_yaml_file_mixed_values(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "mixed.yaml"
    yaml_path.write_text("a:\n  template: A\nb: plain\nc:\n  other: ignored\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 3
    assert registry.get("a").content == "A"
    assert registry.get("b").content == "plain"
    assert registry.get("c").content == ""


def test_load_yaml_file_none_values(tmp_path: Path) -> None:
    registry = PromptRegistry()
    yaml_path = tmp_path / "none.yaml"
    yaml_path.write_text("n: null\n", encoding="utf-8")
    count = registry.load_yaml_file(yaml_path)
    assert count == 1
    assert registry.get("n").content == ""


# =============================================================================
# Integration-like usage patterns
# =============================================================================


def test_full_register_render_cycle() -> None:
    registry = PromptRegistry()
    registry.register("sys", "You are {{ role }}. Task: {{ task }}")
    output = registry.render("sys", {"role": "assistant", "task": "test"})
    assert output == "You are assistant. Task: test"


def test_multiple_templates_isolated() -> None:
    registry = PromptRegistry()
    registry.register("a", "A: {{ x }}")
    registry.register("b", "B: {{ y }}")
    assert registry.render("a", {"x": "1"}) == "A: 1"
    assert registry.render("b", {"y": "2"}) == "B: 2"
