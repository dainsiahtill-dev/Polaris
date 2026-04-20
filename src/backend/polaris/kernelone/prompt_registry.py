"""Jinja2-based prompt registry with strict variable validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jinja2
import yaml
from jinja2 import meta


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    content: str
    required_variables: tuple[str, ...]


class PromptRegistry:
    """Registry for named Jinja2 prompt templates."""

    def __init__(self) -> None:
        self._environment = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
        self._templates: dict[str, PromptTemplate] = {}

    def register(self, name: str, content: str) -> PromptTemplate:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("template name cannot be empty")
        parsed = self._environment.parse(content)
        variables = tuple(sorted(meta.find_undeclared_variables(parsed)))
        template = PromptTemplate(name=normalized_name, content=content, required_variables=variables)
        self._templates[normalized_name] = template
        return template

    def get(self, name: str) -> PromptTemplate:
        template = self._templates.get(name)
        if template is None:
            raise KeyError(f"template not found: {name}")
        return template

    def render(self, name: str, variables: dict[str, Any]) -> str:
        template = self.get(name)
        provided = set(variables.keys())
        required = set(template.required_variables)
        missing = sorted(required - provided)
        if missing:
            raise ValueError(f"missing template variables: {', '.join(missing)}")
        compiled = self._environment.from_string(template.content)
        return compiled.render(**variables)

    def load_yaml_file(self, file_path: str | Path) -> int:
        path = Path(file_path)
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        loaded = 0
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    content = str(value.get("template", value.get("content", "")) or "")
                else:
                    content = str(value or "")
                self.register(str(key), content)
                loaded += 1
        return loaded

    def list_templates(self) -> tuple[str, ...]:
        return tuple(sorted(self._templates.keys()))


__all__ = [
    "PromptRegistry",
    "PromptTemplate",
]
