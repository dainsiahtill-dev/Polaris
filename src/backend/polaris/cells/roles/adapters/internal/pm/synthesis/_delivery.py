"""Delivery-depth / model-target helpers for PM contract synthesis."""

from __future__ import annotations

import re
from typing import Any

from ._checks import _dedupe_limited_texts


def _contract_keyword_tokens(
    keywords: list[str],
    *,
    fallback: tuple[str, ...] = ("entity", "rule", "result", "sample"),
    limit: int = 4,
) -> list[str]:
    tokens: list[str] = []
    for keyword in keywords:
        cleaned = re.sub(r"[^a-z0-9_]+", "_", str(keyword or "").strip().lower()).strip("_")
        if cleaned and not cleaned[0].isdigit():
            tokens.append(cleaned)
    tokens.extend(fallback)
    return _dedupe_limited_texts(tokens, limit=limit)


def _pascal_case_token(token: str, *, fallback: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", str(token or "")) if part]
    if not parts:
        return fallback
    value = "".join(part[:1].upper() + part[1:].lower() for part in parts)
    return value if value[:1].isalpha() else fallback


def _domain_module_name(tokens: list[str], *, suffix: str) -> str:
    head = tokens[0] if tokens else "domain"
    return f"{head}_{suffix}"


def _delivery_depth_contract(
    *,
    domain_label: str,
    language: str,
    project_type: str,
    keywords: list[str],
    checks: list[str],
) -> dict[str, Any]:
    """Build the shared contract that prevents runnable-but-hollow delivery."""

    normalized_keywords = _dedupe_limited_texts([str(item).lower() for item in keywords], limit=6)
    keyword_text = ", ".join(normalized_keywords) if normalized_keywords else str(domain_label)
    return {
        "schema_version": "polaris.delivery_depth_contract.v1",
        "source": "pm.deterministic_synthesis",
        "language": language,
        "project_type": project_type,
        "product_intent": {
            "subject": domain_label,
            "core_user_journey": [
                f"用户通过可执行入口运行 {domain_label} 并获得可观察结果",
                "用户输入或使用一组代表性样例数据触发核心领域规则",
                "系统输出能解释关键决策、计算结果或状态变化，而不是静态占位文本",
            ],
            "primary_entities": normalized_keywords,
        },
        "behavior_contract": {
            "rule_matrix": [
                f"至少实现 3 条与 {keyword_text} 相关的可解释业务规则",
                "每条核心规则必须能被入口或测试观察到输入、处理和输出",
                "规则不能只做字段存在校验或关键词拼接，必须体现需求中的映射、匹配、评分、状态变化或生成逻辑",
            ],
            "sample_dataset": [
                "提供最小但有代表性的示例数据或种子内容",
                "样例必须覆盖正常路径、边界路径和错误路径",
            ],
            "edge_cases": [
                "空输入或缺失字段",
                "未知/不支持的领域值",
                "极端但合法的数值、长度或状态组合",
            ],
        },
        "acceptance_contract": {
            "required_behavior_tests": [
                "至少 1 个正常路径测试",
                "至少 1 个边界情况测试",
                "至少 1 个错误/非法输入测试",
            ],
            "minimum_depth_signals": [
                "核心逻辑与 I/O 或 CLI/Web 入口分离",
                "测试断言业务结果而不是只检查文件存在或关键词存在",
                "README 包含真实运行命令和代表性示例输出",
            ],
            "deterministic_checks": checks,
        },
        "anti_hollow_delivery": [
            "禁止只生成文件骨架、静态打印、关键词堆砌或测试自证空逻辑",
            "禁止让测试只验证源码存在、脚本存在、README 存在或关键词命中",
            "如果需求信息不足，必须基于现有 brief 做最小合理产品规则假设并写入 README/测试",
        ],
    }


def _delivery_plan_document(
    *,
    domain_label: str,
    language: str,
    project_type: str,
    keywords: list[str],
    checks: list[str],
) -> dict[str, Any]:
    normalized_keywords = _dedupe_limited_texts([str(item).lower() for item in keywords], limit=6)
    keyword_text = ", ".join(normalized_keywords) if normalized_keywords else str(domain_label)
    return {
        "schema_version": "polaris.delivery_plan_document.v1",
        "source": "pm.deterministic_synthesis",
        "title": f"{domain_label} 交付计划",
        "language": language,
        "project_type": project_type,
        "product_summary": {
            "intent": f"交付一个真实可运行的 {domain_label}，而不是只满足文件结构检查的脚手架。",
            "core_terms": normalized_keywords,
        },
        "user_journey": [
            f"用户启动 {domain_label} 的 CLI/Web/脚本入口",
            f"用户通过示例数据或输入触发 {keyword_text} 相关核心规则",
            "系统返回可解释的业务结果、状态变化或生成内容",
        ],
        "capability_plan": [
            "领域模型表达主要实体和合法状态",
            "核心引擎实现需求中的映射、匹配、评分、状态变化或生成规则",
            "入口层只做 I/O 编排，不能替代核心业务逻辑",
            "测试层验证行为结果，不能只验证文件存在或关键词命中",
        ],
        "behavior_plan": [
            "定义至少 3 条可观察业务规则",
            "为每条核心规则准备至少 1 个代表性样例",
            "覆盖正常、边界、非法输入三类场景",
        ],
        "verification_plan": [
            "执行语言对应的编译/语法检查",
            "执行真实入口 smoke test",
            "执行行为测试并断言业务输出",
            *checks,
        ],
        "evolution_notes": [
            "当前交付应保持轻量，但模块边界要支持后续增加更多规则和数据源",
            "不要预先强套 MVC/MVVM/微服务等架构；只有实际 UI、持久化或外部 I/O 需要时才引入相应结构",
        ],
    }


def _with_delivery_depth_metadata(
    metadata: dict[str, Any],
    delivery_depth_contract: dict[str, Any],
    delivery_plan_document: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(metadata)
    merged["delivery_plan_document"] = delivery_plan_document
    merged["delivery_depth_contract"] = delivery_depth_contract
    merged["behavior_contract"] = delivery_depth_contract.get("behavior_contract", {})
    return merged


def _append_delivery_depth_to_contracts(
    contracts: list[dict[str, Any]],
    *,
    delivery_plan_document: dict[str, Any],
    delivery_depth_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    implementation_steps = [
        "根据 delivery_depth_contract 落地核心行为矩阵，至少实现 3 条可解释业务规则",
        "提供代表性样例数据或种子内容，让入口输出体现真实业务决策而非静态占位",
        "实现空输入、未知值、非法输入或边界数值的清晰处理路径",
    ]
    verification_steps = [
        "测试必须断言核心业务结果，覆盖正常路径、边界路径和错误路径",
        "验收脚本不得只检查文件存在、关键词存在或脚本存在；必须运行真实入口或核心 API",
    ]
    acceptance_items = [
        "核心引擎至少包含 3 条可观察业务规则，并由入口或测试覆盖",
        "测试覆盖正常路径、边界情况和错误/非法输入，且断言业务结果",
        "交付物不是空骨架、静态打印、关键词堆砌或只靠测试自证的浅实现",
    ]
    updated: list[dict[str, Any]] = []
    for raw in contracts:
        item = dict(raw)
        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        item["metadata"] = _with_delivery_depth_metadata(metadata, delivery_depth_contract, delivery_plan_document)
        steps = list(item.get("steps") or [])
        phase_text = str(item.get("phase") or "").lower()
        title_text = str(item.get("title") or "").lower()
        if any(token in phase_text or token in title_text for token in ("test", "验收", "验证", "verification")):
            steps.extend(step for step in verification_steps if step not in steps)
        else:
            steps.extend(step for step in implementation_steps if step not in steps)
        item["steps"] = steps
        acceptance = list(item.get("acceptance") or item.get("acceptance_criteria") or [])
        acceptance.extend(entry for entry in acceptance_items if entry not in acceptance)
        item["acceptance"] = acceptance
        item["acceptance_criteria"] = acceptance
        updated.append(item)
    return updated


def _pascal_identifier_token(value: str, *, fallback: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    token = "".join(part[:1].upper() + part[1:].lower() for part in parts if part)
    return token or fallback


def _typescript_model_target_from_keyword(keyword: str, *, fallback: str) -> str:
    normalized = str(keyword or "").strip().lower()
    aliases = {
        "moon": "MoonPhase",
        "moonphase": "MoonPhase",
    }
    name = aliases.get(normalized) or _pascal_identifier_token(normalized, fallback=fallback)
    return f"src/models/{name}.ts"


def _typescript_model_targets_from_keywords(keywords: list[str], *, domain_token: str) -> list[str]:
    source_keywords = keywords[:4] if keywords else [domain_token]
    targets = [
        _typescript_model_target_from_keyword(
            keyword,
            fallback=_pascal_identifier_token(domain_token, fallback="DomainModel"),
        )
        for keyword in source_keywords
    ]
    return _dedupe_limited_texts(targets, limit=6)


def _javascript_model_target_from_keyword(keyword: str, *, fallback: str) -> str:
    normalized = str(keyword or "").strip().lower()
    parts = [part.lower() for part in re.split(r"[^A-Za-z0-9]+", normalized) if part]
    fallback_parts = [part.lower() for part in re.split(r"[^A-Za-z0-9]+", fallback) if part]
    name = "-".join(parts or fallback_parts or ["domain"])
    return f"src/{name}.js"


def _javascript_model_targets_from_keywords(keywords: list[str], *, domain_token: str) -> list[str]:
    source_keywords = keywords[:4] if keywords else [domain_token]
    targets = [
        _javascript_model_target_from_keyword(
            keyword,
            fallback=_pascal_identifier_token(domain_token, fallback="DomainModel"),
        )
        for keyword in source_keywords
    ]
    return _dedupe_limited_texts(targets, limit=6)


def _extract_typescript_semantic_keywords(directive: str) -> list[str]:
    text = str(directive or "").lower()
    checks = [
        ("firefly", ("firefly", "fireflies", "萤火虫", "發光昆蟲", "发光昆虫")),
        ("flower", ("flower", "flowers", "花朵", "花園", "花园")),
        ("moonphase", ("moon", "moonphase", "月相", "月亮")),
        ("humidity", ("humidity", "湿度", "濕度")),
    ]
    keywords: list[str] = []
    for keyword, needles in checks:
        if any(needle in text for needle in needles):
            keywords.append(keyword)
    return keywords
