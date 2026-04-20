from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.budget import BudgetGovernor
from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    ShadowTaskRecord,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.registry import ShadowTaskRegistry


@dataclass(frozen=True, slots=True)
class PredictedInvocation:
    """ChainSpeculator 预测的下游工具调用."""

    tool_name: str
    arguments: dict[str, Any]
    predicted_by_tool: str
    predicted_by_task_id: str


class ResultExtractor:
    """从工具结果中启发式提取文件路径和 URL."""

    _URL_RE = re.compile(r"https?://[^\s\"'<>\)\]\}]+", re.IGNORECASE)

    def extract_file_paths(self, tool_result: Any) -> list[str]:
        """从工具结果中提取文件路径列表."""
        paths: list[str] = []
        if isinstance(tool_result, dict):
            # 标准字段
            if "files" in tool_result and isinstance(tool_result["files"], list):
                paths.extend(str(p) for p in tool_result["files"] if p)
            if "matches" in tool_result and isinstance(tool_result["matches"], list):
                for match in tool_result["matches"]:
                    if isinstance(match, dict) and "path" in match:
                        paths.append(str(match["path"]))
            if "results" in tool_result and isinstance(tool_result["results"], list):
                for result in tool_result["results"]:
                    if isinstance(result, dict) and "path" in result:
                        paths.append(str(result["path"]))
            if "paths" in tool_result and isinstance(tool_result["paths"], list):
                paths.extend(str(p) for p in tool_result["paths"] if p)
        elif isinstance(tool_result, list):
            for item in tool_result:
                if isinstance(item, dict) and "path" in item:
                    paths.append(str(item["path"]))
                elif isinstance(item, str):
                    paths.append(item)
        elif isinstance(tool_result, str):
            # 尝试正则提取类似文件路径的行
            for line in tool_result.splitlines():
                line = line.strip()
                if line and ("/" in line or "\\" in line or "." in line) and not line.startswith("http"):
                    # 简单启发式:排除明显是 URL 的行
                    paths.append(line)
        return self._normalize_paths(paths)

    def extract_urls(self, tool_result: Any) -> list[str]:
        """从工具结果中提取 URL 列表."""
        urls: list[str] = []
        if isinstance(tool_result, dict):
            if "urls" in tool_result and isinstance(tool_result["urls"], list):
                urls.extend(str(u) for u in tool_result["urls"] if u)
            if "results" in tool_result and isinstance(tool_result["results"], list):
                for result in tool_result["results"]:
                    if isinstance(result, dict) and "url" in result:
                        urls.append(str(result["url"]))
            if "links" in tool_result and isinstance(tool_result["links"], list):
                urls.extend(str(u) for u in tool_result["links"] if u)
        elif isinstance(tool_result, list):
            for item in tool_result:
                if isinstance(item, dict) and "url" in item:
                    urls.append(str(item["url"]))
                elif isinstance(item, str):
                    urls.append(item)
        elif isinstance(tool_result, str):
            urls = self._URL_RE.findall(tool_result)
        return self._normalize_urls(urls)

    @classmethod
    def _normalize_paths(cls, paths: list[str]) -> list[str]:
        """去重并过滤空路径."""
        seen: set[str] = set()
        out: list[str] = []
        for p in paths:
            p = p.strip()
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out

    @classmethod
    def _normalize_urls(cls, urls: list[str]) -> list[str]:
        """去重、过滤并校验 URL 白名单."""
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            u = u.strip()
            if not u or u in seen:
                continue
            seen.add(u)
            if cls._is_url_allowed(u):
                out.append(u)
        return out

    @classmethod
    def _is_url_allowed(cls, url: str) -> bool:
        """禁止本地、internal/admin 等敏感 URL."""
        lower = url.lower()
        blocked_prefixes = (
            "http://localhost",
            "http://127.",
            "https://localhost",
            "https://127.",
            "http://0.0.0.0",
            "http://192.168.",
            "http://10.",
            "http://172.16.",
            "file://",
        )
        if any(lower.startswith(p) for p in blocked_prefixes):
            return False
        # 禁止 admin/internal 路径
        if "/admin" in lower or "/internal" in lower:
            return False
        # 长度限制
        return len(url) <= 2048


class ChainSpeculator:
    """监听 ShadowTaskRegistry 的 shadow 完成事件，自动触发下游推测.

    支持 retrieval chain(repo_rg -> read_file)和 web prefetch(web_search -> fetch_url)。
    """

    # 上游工具 -> 下游推测策略表
    _CHAIN_POLICY: dict[str, dict[str, Any]] = {
        "repo_rg": {
            "downstream_tool": "read_file",
            "extractor": "file_paths",
            "top_k": 3,
            "tier": "S1",
        },
        "search_code": {
            "downstream_tool": "read_file",
            "extractor": "file_paths",
            "top_k": 3,
            "tier": "S1",
        },
        "web_search": {
            "downstream_tool": "fetch_url",
            "extractor": "urls",
            "top_k": 2,
            "tier": "S2",
        },
    }

    # tier -> 推测策略映射(用于 BudgetGovernor)
    _TIER_POLICY: dict[str, dict[str, Any]] = {
        "S1": {
            "side_effect": "readonly",
            "cost": "cheap",
            "cancellability": "cooperative",
            "reusability": "adoptable",
            "speculate_mode": "speculative_allowed",
        },
        "S2": {
            "side_effect": "readonly",
            "cost": "medium",
            "cancellability": "cooperative",
            "reusability": "adoptable",
            "speculate_mode": "speculative_allowed",
        },
    }

    def __init__(
        self,
        registry: ShadowTaskRegistry,
        budget_governor: BudgetGovernor | None = None,
    ) -> None:
        self._registry = registry
        self._budget_governor = budget_governor
        self._extractor = ResultExtractor()

    def predict_downstream(
        self,
        tool_name: str,
        tool_result: Any,
    ) -> list[PredictedInvocation]:
        """根据上游工具名和结果，预测下游工具调用."""
        normalized = tool_name.strip().lower().replace("-", "_")
        policy = self._CHAIN_POLICY.get(normalized)
        if policy is None:
            return []

        extractor_type = policy["extractor"]
        if extractor_type == "file_paths":
            items = self._extractor.extract_file_paths(tool_result)
        elif extractor_type == "urls":
            items = self._extractor.extract_urls(tool_result)
        else:
            return []

        top_k = policy.get("top_k", 1)
        items = items[:top_k]
        downstream_tool = policy["downstream_tool"]

        predicted: list[PredictedInvocation] = []
        for item in items:
            if downstream_tool == "read_file":
                args = {"path": item}
            elif downstream_tool == "fetch_url":
                args = {"url": item}
            else:
                args = {"target": item}
            predicted.append(
                PredictedInvocation(
                    tool_name=downstream_tool,
                    arguments=args,
                    predicted_by_tool=tool_name,
                    predicted_by_task_id="",
                )
            )
        return predicted

    async def on_shadow_completed(
        self,
        record: ShadowTaskRecord,
    ) -> list[ShadowTaskRecord]:
        """Registry shadow 完成后的回调：触发下游推测.

        Args:
            record: 已完成的上游 shadow task 记录

        Returns:
            新创建的下游 shadow task 记录列表
        """
        if record.state.value != "completed":
            return []

        predicted = self.predict_downstream(record.tool_name, record.result)
        if not predicted:
            return []

        created: list[ShadowTaskRecord] = []
        tier = self._CHAIN_POLICY.get(record.tool_name.strip().lower().replace("-", "_"), {}).get("tier", "S1")
        tier_config = self._TIER_POLICY.get(tier, self._TIER_POLICY["S1"])

        for inv in predicted:
            normalized_args = normalize_args(inv.tool_name, inv.arguments)
            env_fp = build_env_fingerprint()
            spec_key = build_spec_key(
                tool_name=inv.tool_name,
                normalized_args=normalized_args,
                env_fingerprint=env_fp,
            )

            if self._registry.exists_active(spec_key):
                continue

            policy = ToolSpecPolicy(
                tool_name=inv.tool_name,
                side_effect=tier_config["side_effect"],
                cost=tier_config["cost"],
                cancellability=tier_config["cancellability"],
                reusability=tier_config["reusability"],
                speculate_mode=tier_config["speculate_mode"],
            )
            try:
                downstream_record = await self._registry.start_shadow_task(
                    turn_id=record.origin_turn_id,
                    candidate_id=f"chain_{record.origin_candidate_id}",
                    tool_name=inv.tool_name,
                    normalized_args=normalized_args,
                    spec_key=spec_key,
                    env_fingerprint=env_fp,
                    policy=policy,
                    parent_task_id=record.task_id,
                )
                created.append(downstream_record)
            except RuntimeError:
                # budget denied 或其他启动失败,跳过
                continue

        return created
