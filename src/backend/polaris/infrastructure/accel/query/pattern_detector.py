from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..storage.index_cache import load_index_rows

_VALID_PATTERN_TYPES = {"singleton", "factory", "observer", "decorator", "builder"}


from ..utils import normalize_path_str as _normalize_path

if TYPE_CHECKING:
    from pathlib import Path


def detect_patterns(
    index_dir: Path,
    pattern_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect design patterns in the codebase.

    Args:
        index_dir: Path to the index directory.
        pattern_types: Optional list of pattern types to detect.
                       Supported: singleton, factory, observer, decorator, builder.
                       If None, detects all supported patterns.

    Returns:
        List of detected patterns with confidence scores.
    """
    type_filter: set[str] | None = None
    if pattern_types:
        type_filter = {t.lower().strip() for t in pattern_types if t.lower().strip() in _VALID_PATTERN_TYPES}
        if not type_filter:
            type_filter = None

    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []

    for row in all_symbols:
        kind = str(row.get("kind", "")).strip().lower()
        if kind == "class":
            classes.append(row)
        elif kind in {"function", "method"}:
            functions.append(row)

    patterns: list[dict[str, Any]] = []

    if type_filter is None or "singleton" in type_filter:
        patterns.extend(_detect_singleton(classes, functions))

    if type_filter is None or "factory" in type_filter:
        patterns.extend(_detect_factory(classes, functions))

    if type_filter is None or "observer" in type_filter:
        patterns.extend(_detect_observer(classes, functions))

    if type_filter is None or "decorator" in type_filter:
        patterns.extend(_detect_decorator(classes, functions))

    if type_filter is None or "builder" in type_filter:
        patterns.extend(_detect_builder(classes, functions))

    patterns.sort(key=lambda p: (-p.get("confidence", 0), p.get("symbol", "")))

    return patterns


def _detect_singleton(classes: list[dict], functions: list[dict]) -> list[dict[str, Any]]:
    """Detect Singleton pattern.

    Heuristics:
    - Class has _instance attribute or similar
    - Class has get_instance/getInstance method
    - Class uses __new__ with instance caching
    """
    patterns: list[dict[str, Any]] = []

    class_methods: dict[str, list[str]] = {}
    for func in functions:
        scope = str(func.get("scope", "")).strip()
        if scope:
            class_methods.setdefault(scope.lower(), []).append(str(func.get("symbol", "")).strip().lower())

    for cls in classes:
        symbol = str(cls.get("symbol", "")).strip()
        qualified = str(cls.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(cls.get("file", "")))

        confidence = 0.0
        indicators: list[str] = []

        attributes = cls.get("attributes", [])
        if isinstance(attributes, list):
            for attr in attributes:
                attr_lower = str(attr).lower()
                if "_instance" in attr_lower or attr_lower == "instance":
                    confidence += 0.4
                    indicators.append("has_instance_attribute")
                    break

        methods = class_methods.get(symbol.lower(), [])
        methods.extend(class_methods.get(qualified.lower(), []))

        singleton_methods = {"get_instance", "getinstance", "instance", "get_singleton"}
        for method in methods:
            if method in singleton_methods:
                confidence += 0.4
                indicators.append(f"has_{method}_method")
                break

        if "__new__" in methods:
            confidence += 0.2
            indicators.append("overrides___new__")

        if confidence >= 0.4:
            patterns.append(
                {
                    "pattern": "singleton",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(cls.get("line_start", 1)),
                    "confidence": round(min(1.0, confidence), 2),
                    "indicators": indicators,
                }
            )

    return patterns


def _detect_factory(classes: list[dict], functions: list[dict]) -> list[dict[str, Any]]:
    """Detect Factory pattern.

    Heuristics:
    - Function/method name contains create_, make_, build_
    - Function returns object (heuristic: has return type hint)
    - Class named *Factory
    """
    patterns: list[dict[str, Any]] = []
    factory_prefixes = {"create_", "make_", "build_", "new_", "construct_"}

    for func in functions:
        symbol = str(func.get("symbol", "")).strip()
        symbol_lower = symbol.lower()
        qualified = str(func.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(func.get("file", "")))

        confidence = 0.0
        indicators: list[str] = []

        for prefix in factory_prefixes:
            if symbol_lower.startswith(prefix):
                confidence += 0.5
                indicators.append(f"name_starts_with_{prefix.rstrip('_')}")
                break

        return_type = str(func.get("return_type", "")).strip()
        if return_type and return_type not in {"None", "void", "bool", "int", "str", "float"}:
            confidence += 0.3
            indicators.append("returns_object")

        if confidence >= 0.5:
            patterns.append(
                {
                    "pattern": "factory",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(func.get("line_start", 1)),
                    "confidence": round(min(1.0, confidence), 2),
                    "indicators": indicators,
                }
            )

    for cls in classes:
        symbol = str(cls.get("symbol", "")).strip()
        qualified = str(cls.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(cls.get("file", "")))

        if symbol.lower().endswith("factory"):
            patterns.append(
                {
                    "pattern": "factory",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(cls.get("line_start", 1)),
                    "confidence": 0.8,
                    "indicators": ["class_name_ends_with_Factory"],
                }
            )

    return patterns


def _detect_observer(classes: list[dict], functions: list[dict]) -> list[dict[str, Any]]:
    """Detect Observer pattern.

    Heuristics:
    - Class has subscribe/unsubscribe/notify methods
    - Class has add_listener/remove_listener/notify_listeners methods
    - Class has on_* event handler methods
    """
    patterns: list[dict[str, Any]] = []

    class_methods: dict[str, list[str]] = {}
    for func in functions:
        scope = str(func.get("scope", "")).strip()
        if scope:
            class_methods.setdefault(scope.lower(), []).append(str(func.get("symbol", "")).strip().lower())

    observer_methods_set1 = {"subscribe", "unsubscribe", "notify"}
    observer_methods_set2 = {"add_listener", "remove_listener", "notify_listeners"}
    observer_methods_set3 = {"add_observer", "remove_observer", "notify_observers"}
    observer_methods_set4 = {"attach", "detach", "notify"}

    for cls in classes:
        symbol = str(cls.get("symbol", "")).strip()
        qualified = str(cls.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(cls.get("file", "")))

        methods = set(class_methods.get(symbol.lower(), []))
        methods.update(class_methods.get(qualified.lower(), []))

        confidence = 0.0
        indicators: list[str] = []

        for method_set, name in [
            (observer_methods_set1, "subscribe_pattern"),
            (observer_methods_set2, "listener_pattern"),
            (observer_methods_set3, "observer_pattern"),
            (observer_methods_set4, "attach_pattern"),
        ]:
            matches = methods & method_set
            if len(matches) >= 2:
                confidence = max(confidence, 0.7)
                indicators.append(name)

        on_methods = [m for m in methods if m.startswith("on_") and len(m) > 3]
        if len(on_methods) >= 3:
            confidence = max(confidence, 0.5)
            indicators.append(f"has_{len(on_methods)}_on_methods")

        if confidence >= 0.5:
            patterns.append(
                {
                    "pattern": "observer",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(cls.get("line_start", 1)),
                    "confidence": round(min(1.0, confidence), 2),
                    "indicators": indicators,
                }
            )

    return patterns


def _detect_decorator(classes: list[dict], functions: list[dict]) -> list[dict[str, Any]]:
    """Detect Decorator pattern.

    Heuristics:
    - Class wraps another object of same interface
    - Class delegates to wrapped object
    - Function is a decorator (has @functools.wraps or similar)
    """
    patterns: list[dict[str, Any]] = []

    for func in functions:
        symbol = str(func.get("symbol", "")).strip()
        qualified = str(func.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(func.get("file", "")))

        decorators = func.get("decorators", [])
        if not isinstance(decorators, list):
            decorators = []

        confidence = 0.0
        indicators: list[str] = []

        for dec in decorators:
            dec_lower = str(dec).lower()
            if "wraps" in dec_lower:
                confidence += 0.6
                indicators.append("uses_functools_wraps")
                break

        params = func.get("parameters", [])
        if isinstance(params, list) and len(params) == 1:
            param = str(params[0]).lower()
            if param in {"func", "fn", "f", "function", "callable"}:
                confidence += 0.3
                indicators.append("single_callable_param")

        return_type = str(func.get("return_type", "")).strip().lower()
        if return_type in {"callable", "wrapper"}:
            confidence += 0.2
            indicators.append("returns_callable")

        if confidence >= 0.5:
            patterns.append(
                {
                    "pattern": "decorator",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(func.get("line_start", 1)),
                    "confidence": round(min(1.0, confidence), 2),
                    "indicators": indicators,
                }
            )

    for cls in classes:
        symbol = str(cls.get("symbol", "")).strip()
        if symbol.lower().endswith("decorator"):
            patterns.append(
                {
                    "pattern": "decorator",
                    "symbol": symbol,
                    "qualified_name": str(cls.get("qualified_name", "")).strip(),
                    "file": _normalize_path(str(cls.get("file", ""))),
                    "line_start": int(cls.get("line_start", 1)),
                    "confidence": 0.7,
                    "indicators": ["class_name_ends_with_Decorator"],
                }
            )

    return patterns


def _detect_builder(classes: list[dict], functions: list[dict]) -> list[dict[str, Any]]:
    """Detect Builder pattern.

    Heuristics:
    - Class has build() method
    - Class has multiple with_* or set_* methods
    - Class name ends with Builder
    """
    patterns: list[dict[str, Any]] = []

    class_methods: dict[str, list[str]] = {}
    for func in functions:
        scope = str(func.get("scope", "")).strip()
        if scope:
            class_methods.setdefault(scope.lower(), []).append(str(func.get("symbol", "")).strip().lower())

    for cls in classes:
        symbol = str(cls.get("symbol", "")).strip()
        qualified = str(cls.get("qualified_name", "")).strip()
        file_path = _normalize_path(str(cls.get("file", "")))

        methods = class_methods.get(symbol.lower(), [])
        methods.extend(class_methods.get(qualified.lower(), []))

        confidence = 0.0
        indicators: list[str] = []

        if symbol.lower().endswith("builder"):
            confidence += 0.5
            indicators.append("class_name_ends_with_Builder")

        if "build" in methods:
            confidence += 0.3
            indicators.append("has_build_method")

        with_methods = [m for m in methods if m.startswith("with_")]
        set_methods = [m for m in methods if m.startswith("set_") and m != "set"]

        if len(with_methods) >= 2:
            confidence += 0.3
            indicators.append(f"has_{len(with_methods)}_with_methods")
        if len(set_methods) >= 3:
            confidence += 0.2
            indicators.append(f"has_{len(set_methods)}_set_methods")

        if confidence >= 0.5:
            patterns.append(
                {
                    "pattern": "builder",
                    "symbol": symbol,
                    "qualified_name": qualified,
                    "file": file_path,
                    "line_start": int(cls.get("line_start", 1)),
                    "confidence": round(min(1.0, confidence), 2),
                    "indicators": indicators,
                }
            )

    return patterns
