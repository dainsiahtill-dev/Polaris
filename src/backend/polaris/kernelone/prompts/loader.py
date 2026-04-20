import json
import os
import re
from functools import lru_cache

PROFILE_ENV = "KERNELONE_PROMPT_PROFILE"
DEFAULT_PROFILE = "zhenguan_governance"


def normalize_escaped_multiline(text: str) -> tuple[str, bool]:
    """
    规范化被转义的多行文本。

    当字符串"包含 \\n 且不包含真实换行 \n 且 \\n 数量 >= 2"时执行解码。
    解码仅处理 \\r\\n、\\n、\\t，不做通用 unicode_escape，避免误伤。

    Returns:
        Tuple[规范化后的文本, 是否进行了规范化]
    """
    if not isinstance(text, str):
        return text, False

    # 检查是否包含字面量 \n
    escaped_newline_count = text.count("\\n")

    # 检查是否包含真实换行
    has_real_newline = "\n" in text

    # 只有当包含字面量 \n、不包含真实换行、且 \n 数量 >= 2 时才规范化
    if escaped_newline_count < 2 or has_real_newline:
        return text, False

    # 执行规范化：仅处理 \r\n、\n、\t
    normalized = text
    normalized = normalized.replace("\\r\\n", "\r\n")
    normalized = normalized.replace("\\n", "\n")
    normalized = normalized.replace("\\t", "\t")

    return normalized, True


def _templates_dir() -> str:
    module_dir = os.path.dirname(__file__)
    candidates = [
        os.path.abspath(os.path.join(module_dir, "..", "..", "prompts")),
        os.path.abspath(os.path.join(module_dir, "..", "..", "..", "..", "prompts")),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def current_profile() -> str:
    value = os.environ.get(PROFILE_ENV, DEFAULT_PROFILE).strip()
    return value or DEFAULT_PROFILE


@lru_cache(maxsize=8)
def load_profile(profile: str | None = None) -> dict[str, object]:
    profile_name = (profile or current_profile()).strip()
    templates_dir = _templates_dir()
    candidate = os.path.join(templates_dir, f"{profile_name}.json")
    if not os.path.isfile(candidate):
        fallback = os.path.join(templates_dir, f"{DEFAULT_PROFILE}.json")
        if os.path.isfile(fallback):
            candidate = fallback
    with open(candidate, encoding="utf-8") as handle:
        return json.load(handle)


def get_template(name: str, profile: str | None = None) -> str:
    profile_name = (profile or current_profile()).strip()
    data = load_profile(profile_name)
    if name == "plan_template":
        template = data.get("plan_template")
        if isinstance(template, str):
            # 规范化被转义的多行文本
            normalized, _ = normalize_escaped_multiline(template)
            return normalized
    templates = data.get("templates")
    if isinstance(templates, dict):
        template = templates.get(name)
        if isinstance(template, str):
            # 规范化被转义的多行文本
            normalized, _ = normalize_escaped_multiline(template)
            return normalized
    raise KeyError(f"Prompt template not found: {name}")


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_template(template: str, values: dict[str, object]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = values.get(key, "")
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(replace, template)
