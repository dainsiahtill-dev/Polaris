"""Hot-reload wrapper for PromptRegistry."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from polaris.kernelone.prompt_registry import PromptRegistry


class HotReloadPromptRegistry(PromptRegistry):
    """Prompt registry with file-based hot reload support."""

    def __init__(self, files: list[str | Path]) -> None:
        super().__init__()
        self._files = [Path(path) for path in files]
        self._mtimes: dict[Path, float] = {}
        self.reload_all()

    def reload_all(self) -> dict[str, Any]:
        loaded_templates = 0
        started_ns = time.perf_counter_ns()
        for path in self._files:
            loaded_templates += self.load_yaml_file(path)
            self._mtimes[path] = path.stat().st_mtime if path.exists() else 0.0
        latency_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000.0
        return {
            "loaded_templates": loaded_templates,
            "reload_latency_s": latency_s,
        }

    def reload_if_changed(self) -> dict[str, Any]:
        changed = False
        for path in self._files:
            current = path.stat().st_mtime if path.exists() else 0.0
            previous = self._mtimes.get(path, -1.0)
            if current != previous:
                changed = True
                break

        if not changed:
            return {"changed": False, "reload_latency_s": 0.0, "loaded_templates": 0}

        result = self.reload_all()
        result["changed"] = True
        return result


__all__ = [
    "HotReloadPromptRegistry",
]
