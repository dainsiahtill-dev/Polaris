from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.infrastructure.llm.providers import provider_manager
from polaris.infrastructure.llm.providers.codex_cli_provider import CodexCLIProvider
from polaris.kernelone.llm.provider_contract import KernelLLMRuntimeAdapter
from polaris.kernelone.llm.runtime import invoke_role_runtime_provider


ROOT = Path(r"C:\Users\dains\Documents\GitLab\polaris")
WORKSPACE = ROOT / ".understand-thing" / "tmp" / "codex_director_smoke_workspace"
OUTPUT = ROOT / ".understand-thing" / "tmp" / "codex_cli_director_smoke_20260527.json"


class SmokeAdapter(KernelLLMRuntimeAdapter):
    def __init__(self, *, model: str) -> None:
        self.model = model

    def get_role_model(self, role: str) -> tuple[str, str]:
        if role != "director":
            return "", ""
        return "codex_cli", self.model

    def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, Any]:
        del workspace
        if provider_id != "codex_cli":
            return {}
        cfg = CodexCLIProvider.get_default_config()
        cfg["timeout"] = 180
        cfg["health_args"] = ["--version"]
        codex_exec = dict(cfg.get("codex_exec") or {})
        codex_exec.update(
            {
                "cd": str(WORKSPACE),
                "sandbox": "read-only",
                "skip_git_repo_check": True,
                "json": True,
                "full_auto": False,
                "yolo": False,
            }
        )
        cfg["codex_exec"] = codex_exec
        return cfg

    def get_provider_instance(self, provider_type: str) -> Any:
        return provider_manager.get_provider_instance(provider_type)

    def record_provider_failure(self, provider_type: str) -> None:
        del provider_type


async def _apply_director_text_patch(text: str) -> dict[str, Any]:
    executor = DirectorPatchExecutor(str(WORKSPACE))
    results = await executor.execute_tools(
        text,
        "codex-cli-smoke",
        lambda *_args, **_kwargs: None,
    )
    target = WORKSPACE / "src" / "smoke.txt"
    return {
        "tool_results": results,
        "file_exists": target.is_file(),
        "file_content": target.read_text(encoding="utf-8") if target.is_file() else "",
    }


async def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    (WORKSPACE / "src").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "README.md").write_text("# Codex Director smoke\n", encoding="utf-8")

    prompt = "\n".join(
        [
            "You are testing Polaris Director text-patch fallback.",
            "Do not edit files directly.",
            "Return only this exact file block and no explanation:",
            "src/smoke.txt",
            "```txt",
            "codex-director-smoke",
            "```",
        ]
    )
    model = "gpt-5.3-codex"
    adapter = SmokeAdapter(model=model)

    blocked = invoke_role_runtime_provider(
        role="director",
        workspace=str(WORKSPACE),
        prompt=prompt,
        fallback_model="",
        timeout=30,
        adapter=adapter,
        blocked_provider_types={"codex_cli", "codex_sdk"},
    )

    unblocked = invoke_role_runtime_provider(
        role="director",
        workspace=str(WORKSPACE),
        prompt=prompt,
        fallback_model="",
        timeout=180,
        adapter=adapter,
        blocked_provider_types=set(),
    )
    applied = await _apply_director_text_patch(unblocked.output if unblocked.ok else "")
    report = {
        "workspace": str(WORKSPACE),
        "model": model,
        "blocked_path": asdict(blocked),
        "unblocked_path": {
            **asdict(unblocked),
            "output_preview": str(unblocked.output or "")[:1200],
            "output_length": len(str(unblocked.output or "")),
        },
        "director_patch_apply": applied,
        "pass": bool(unblocked.ok and applied["file_exists"] and applied["file_content"].strip() == "codex-director-smoke"),
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
