"""Utility helpers for tool-calling matrix fixtures."""


def normalize_name(value: str) -> str:
    return value.strip().lower()


def build_slug(namespace: str, name: str) -> str:
    return f"{normalize_name(namespace)}-{normalize_name(name)}"


def default_headers() -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-harborpilot-source": "tool-calling-matrix",
    }


def compute_checksum(items: list[str]) -> int:
    checksum = 0
    for item in items:
        for ch in item:
            checksum += ord(ch)
    return checksum


def stable_join(parts: list[str], sep: str = "/") -> str:
    filtered = [item for item in parts if item]
    return sep.join(filtered)


def clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def maybe_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def chunk_list(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        raise ValueError("size must be > 0")
    return [items[i : i + size] for i in range(0, len(items), size)]


def ensure_prefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value
    return f"{prefix}{value}"


def strip_prefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def is_python_file(path: str) -> bool:
    return path.endswith(".py")


def is_typescript_file(path: str) -> bool:
    return path.endswith(".ts")


def collect_todo_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if "TODO" in line]


def count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def noop() -> None:
    return None
