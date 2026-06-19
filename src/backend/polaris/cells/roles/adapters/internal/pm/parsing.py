"""PM 合同解析 mixin：从 LLM 文本/JSON/分节/列表中抽取任务合同。

本 mixin 由 :class:`PMAdapter` 组合；方法体与原 ``pm_adapter.py`` 100% 一致（无损迁移）。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from ._protocol import _PMAdapterMixinBase
from .pm_text_utils import (
    _PM_DETAIL_BULLET_PREFIX,
    _TASK_LINE_PREFIX,
    _TASK_SECTION_HEADING,
    _pm_is_dependency_chain_text,
    _pm_is_prompt_echo_response,
    _pm_raw_task_is_dependency_chain,
    _pm_raw_task_is_meta_diagnostic,
    _pm_raw_task_is_non_delivery_constraint,
    _pm_strip_markdown_title_noise,
    _pm_strip_task_label_prefix,
)


class PMContractParsingMixin(_PMAdapterMixinBase):
    """PM 合同解析 mixin：从 LLM 文本/JSON/分节/列表中抽取任务合同。"""

    def _extract_task_contracts(
        self,
        response: str,
        *,
        directive: str,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if _pm_is_prompt_echo_response(str(response or "")):
            return []
        payload = self._extract_json_payload(response)
        raw_tasks: list[Any] = self._extract_tasks_from_payload(payload)

        if raw_tasks:
            contracts: list[dict[str, Any]] = []
            non_delivery_constraint_skips = 0
            for item in raw_tasks:
                if not isinstance(item, dict):
                    continue
                if _pm_raw_task_is_dependency_chain(item):
                    continue
                if _pm_raw_task_is_meta_diagnostic(item):
                    continue
                if _pm_raw_task_is_non_delivery_constraint(item):
                    non_delivery_constraint_skips += 1
                    continue
                contracts.append(self._normalize_task_contract(item, len(contracts) + 1, directive))
            if non_delivery_constraint_skips >= 2 and len(contracts) < 2:
                return []
            return self._apply_projection_contract_hint(
                [item for item in contracts if item],
                projection_hint=projection_hint,
            )

        section_contracts = self._extract_tasks_from_sections(response, directive=directive)
        if section_contracts:
            return self._apply_projection_contract_hint(
                section_contracts,
                projection_hint=projection_hint,
            )

        return self._apply_projection_contract_hint(
            self._extract_tasks_from_bullets(response, directive=directive),
            projection_hint=projection_hint,
        )

    @classmethod
    def _extract_tasks_from_payload(cls, payload: Any) -> list[Any]:
        if payload is None:
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        queue: list[Any] = [payload]
        visited: set[int] = set()
        candidate_keys = (
            "tasks",
            "task_list",
            "tasklist",
            "work_items",
            "workitems",
            "items",
            "todo",
            "todos",
            "deliverables",
            "backlog",
            "plan",
        )

        while queue:
            node = queue.pop(0)
            marker = id(node)
            if marker in visited:
                continue
            visited.add(marker)

            if isinstance(node, list):
                dict_items = [item for item in node if isinstance(item, dict)]
                if dict_items:
                    return dict_items
                for item in node:
                    if isinstance(item, (dict, list)):
                        queue.append(item)
                continue

            if not isinstance(node, dict):
                continue

            for key in candidate_keys:
                items = node.get(key)
                if isinstance(items, list):
                    dict_items = [item for item in items if isinstance(item, dict)]
                    if dict_items:
                        return dict_items

            mapped_tasks = [
                value
                for key, value in node.items()
                if isinstance(value, dict)
                and re.match(r"^(?:task[-_ ]*\d+|t[-_ ]*\d+)$", str(key or "").strip(), re.IGNORECASE)
            ]
            if mapped_tasks:
                return mapped_tasks

            for value in node.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)

        return []

    @staticmethod
    def _extract_json_payload(response: str) -> Any:
        text = str(response or "").strip()
        if not text:
            return None
        if _pm_is_prompt_echo_response(text):
            return None
        candidates = [text]
        fenced_blocks = re.findall(
            r"```(?:json|yaml|yml|markdown|md)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        candidates.extend(item.strip() for item in fenced_blocks if item.strip())
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except (RuntimeError, ValueError):
                decoder = json.JSONDecoder()
                for index, char in enumerate(candidate):
                    if char not in "{[":
                        continue
                    try:
                        parsed, _end = decoder.raw_decode(candidate[index:])
                    except (RuntimeError, ValueError):
                        continue
                    return parsed
                try:
                    parsed = ast.literal_eval(candidate)
                except (RuntimeError, SyntaxError, ValueError):
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    return parsed
        return None

    def _extract_tasks_from_sections(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        active_list_field = ""

        for raw_line in str(response or "").splitlines():
            line = str(raw_line or "").rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            heading_match = _TASK_SECTION_HEADING.match(stripped)
            if heading_match:
                if current:
                    sections.append(current)
                title = str(heading_match.group(2) or "").strip()
                current = {
                    "title": title or f"Task {len(sections) + 1}",
                    "description": "",
                    "steps": [],
                    "acceptance": [],
                    "depends_on": [],
                }
                active_list_field = ""
                continue

            if current is None:
                continue

            key_match = re.match(
                r"^\s*([a-zA-Z_][a-zA-Z0-9_ ]*|目标|描述|范围|步骤|验收标准|验收|依赖|阶段)\s*[:：]\s*(.*)$", stripped
            )
            if key_match:
                key = str(key_match.group(1) or "").strip().lower()
                value = str(key_match.group(2) or "").strip()
                if key in {"title", "任务", "task"}:
                    if value:
                        current["title"] = value
                    active_list_field = ""
                    continue
                if key in {"goal", "目标"}:
                    current["goal"] = value
                    active_list_field = ""
                    continue
                if key in {"description", "描述"}:
                    current["description"] = value
                    active_list_field = ""
                    continue
                if key in {"scope", "范围"}:
                    current["scope"] = value
                    active_list_field = ""
                    continue
                if key in {"phase", "阶段"}:
                    current["phase"] = value
                    active_list_field = ""
                    continue
                if key in {"depends_on", "依赖"}:
                    current["depends_on"] = self._normalize_list(value)
                    active_list_field = ""
                    continue
                if key in {"steps", "步骤", "执行步骤"}:
                    current["steps"] = self._normalize_list(value)
                    active_list_field = "steps"
                    continue
                if key in {"acceptance", "acceptance_criteria", "验收", "验收标准"}:
                    current["acceptance"] = self._normalize_list(value)
                    active_list_field = "acceptance"
                    continue

            bullet_match = re.match(r"^\s*[-*]\s+(.*)$", stripped)
            if bullet_match and active_list_field in {"steps", "acceptance"}:
                item = str(bullet_match.group(1) or "").strip()
                if item:
                    rows = current.get(active_list_field)
                    if not isinstance(rows, list):
                        rows = []
                    rows.append(item)
                    current[active_list_field] = rows
                continue

            if active_list_field in {"steps", "acceptance"}:
                rows = current.get(active_list_field)
                if not isinstance(rows, list):
                    rows = []
                rows.extend(self._normalize_list(stripped))
                current[active_list_field] = [item for item in rows if str(item).strip()]
                continue

            desc = str(current.get("description") or "").strip()
            current["description"] = f"{desc} {stripped}".strip() if desc else stripped

        if current:
            sections.append(current)

        contracts = [
            self._normalize_task_contract(section, idx + 1, directive)
            for idx, section in enumerate(sections)
            if isinstance(section, dict)
        ]
        return [item for item in contracts if item]

    def _extract_tasks_from_bullets(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        contracts: list[dict[str, Any]] = []
        for line in str(response or "").splitlines():
            token = line.strip()
            if not token:
                continue
            match = _TASK_LINE_PREFIX.match(token)
            if not match:
                continue
            payload = token[match.end() :].strip()
            payload = re.sub(r"^\*\*(.*?)\*\*$", r"\1", payload).strip()
            payload = re.sub(r"^`(.*?)`$", r"\1", payload).strip()
            payload = payload.lstrip("- ").strip()
            payload_without_label = _pm_strip_task_label_prefix(payload)
            if payload_without_label:
                payload = payload_without_label
            if _PM_DETAIL_BULLET_PREFIX.match(_pm_strip_markdown_title_noise(payload)):
                continue
            if _pm_is_dependency_chain_text(payload):
                continue
            if not payload:
                continue
            if ":" in payload or "：" in payload:
                title, desc = re.split(r"[:：]", payload, maxsplit=1)
            elif re.search(r"\s+[—–-]\s+", payload):
                title, desc = re.split(r"\s+[—–-]\s+", payload, maxsplit=1)
            else:
                title, desc = payload, ""
            contracts.append(
                self._normalize_task_contract(
                    {
                        "title": title.strip(),
                        "description": desc.strip(),
                    },
                    len(contracts) + 1,
                    directive,
                )
            )
        return [item for item in contracts if item]
