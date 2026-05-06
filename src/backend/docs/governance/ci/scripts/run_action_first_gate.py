#!/usr/bin/env python3
"""Action-First Agent 架构门禁。

验证 Prompt 模板、Parser、Error Recovery 全部正常工作。
用法:
    python run_action_first_gate.py --workspace . --mode all
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".", help="仓库根目录")
    parser.add_argument("--mode", default="all", choices=["all", "prompt", "parser", "recovery"])
    args = parser.parse_args()

    _BACKEND_ROOT = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(_BACKEND_ROOT))

    results = []

    if args.mode in ("all", "prompt"):
        try:
            from polaris.cells.roles.kernel.internal.prompt_templates import (
                build_action_first_prompt,
                get_persona_registry,
            )
            from polaris.kernelone.storage.persona_store import load_workspace_persona

            registry = get_persona_registry()
            persona_id = load_workspace_persona(args.workspace, list(registry.keys()))
            prompt = build_action_first_prompt(persona_id)

            assert "<thinking>" in prompt, "Missing <thinking> block"
            assert "[Action]:" in prompt, "Missing [Action] block"
            assert "【行动优先】" in prompt, "Missing 行动优先 rule"
            assert "【EAFP强制】" in prompt, "Missing EAFP rule"
            assert "【闭环交付】" in prompt, "Missing 闭环 rule"

            print("[PASS] Prompt template")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Prompt template: {e}")
            results.append(False)

    if args.mode in ("all", "parser"):
        try:
            from polaris.cells.roles.kernel.internal.output.action_parser import (
                extract_thinking_block,
                parse_action_block,
            )

            test_text = """[Action]: repo_tree
[Arguments]: {"path": "."}
[Status]: In Progress
[Marker]: None"""

            block = parse_action_block(test_text)
            assert block is not None, "Failed to parse action block"
            assert block.tool_name == "repo_tree"
            assert block.arguments == {"path": "."}

            thinking = extract_thinking_block("<thinking>test</thinking>")
            assert thinking == "test"

            print("[PASS] Action parser")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Action parser: {e}")
            results.append(False)

    if args.mode in ("all", "recovery"):
        try:
            from polaris.cells.roles.kernel.internal.error_recovery.context_injector import (
                ErrorContextInjector,
            )
            from polaris.cells.roles.kernel.internal.error_recovery.retry_policy import (
                RetryPolicy,
                ToolError,
            )

            policy = RetryPolicy()
            error = ToolError("test", "error", {})

            assert policy.should_retry(error, 0) is True
            assert policy.should_retry(error, 3) is False  # max_retries=3

            history = []
            new_history = ErrorContextInjector.inject_error_context(
                history, "read_file", "File not found", {"path": "test.py"}
            )
            assert len(new_history) == 1

            print("[PASS] Error recovery")
            results.append(True)
        except Exception as e:
            print(f"[FAIL] Error recovery: {e}")
            results.append(False)

    if all(results):
        print("\n[ALL PASS] Action-First Agent architecture gate passed")
        sys.exit(0)
    else:
        print("\n[FAIL] Some gates failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
