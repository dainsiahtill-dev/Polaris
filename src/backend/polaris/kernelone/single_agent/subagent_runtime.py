"""Subagent spawning with context isolation.

Phase 5 implementation from learn-claude-code integration.
Parent maintains conversation history, child gets fresh messages=[].
Key insight: "Process isolation = context isolation"

设计约束：
- KernelOne 通用子代理系统，不嵌入特定产品命名
- temp_prefix 由调用方注入，默认 "agent"（非产品名）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_runtime_path

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class SubagentResult:
    """Result from subagent execution."""

    task_id: str
    success: bool
    result: str
    events: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    context_pollution_score: int = 0  # Estimate of how much this would have polluted parent context


@dataclass
class SubagentConfig:
    """Configuration for subagent spawning."""

    max_iterations: int = 50
    timeout_seconds: int = 600
    inherit_tools: bool = True
    allowed_tools: list[str] | None = None  # None = all
    isolated_workspace: bool = False  # Create temp workspace


class SubagentSpawner:
    """Spawn isolated subagents for complex tasks.

    Prevents context pollution by giving subagent fresh message context.
    Parent only receives final result, not intermediate steps.
    """

    def __init__(
        self,
        workspace: str,
        llm_client=None,
        model: str = "",
        tool_handlers: dict[str, Callable] | None = None,
        *,
        temp_prefix: str | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.llm_client = llm_client
        self.model = model
        self.tool_handlers = tool_handlers or {}
        self._temp_prefix = temp_prefix or os.environ.get("KERNELONE_AGENT_TEMP_PREFIX", "agent")

        self._subagent_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/subagents"))
        self._subagent_dir.mkdir(parents=True, exist_ok=True)

        self._active_subagents: dict[str, Any] = {}

    def spawn(
        self,
        task_description: str,
        context: dict[str, Any],
        config: SubagentConfig | None = None,
    ) -> SubagentResult:
        """Spawn a subagent to handle a task in isolation.

        Args:
            task_description: What the subagent should accomplish
            context: Relevant context (files, code snippets, etc.)
            config: Subagent behavior configuration

        Returns:
            SubagentResult with success status and result
        """
        config = config or SubagentConfig()
        subagent_id = f"sub-{uuid.uuid4().hex[:8]}"

        start_time = time.monotonic()

        # Create isolated workspace if requested
        work_dir = self._create_workspace(subagent_id, config.isolated_workspace)

        # Prepare subagent system prompt
        system_prompt = self._build_subagent_prompt(task_description, context)

        # Run subagent loop
        try:
            result = self._run_subagent_loop(
                subagent_id=subagent_id,
                system_prompt=system_prompt,
                work_dir=work_dir,
                config=config,
            )

            duration_ms = int((time.monotonic() - start_time) * 1000)

            return SubagentResult(
                task_id=subagent_id,
                success=result["success"],
                result=result["content"],
                events=result.get("events", []),
                duration_ms=duration_ms,
                context_pollution_score=self._estimate_pollution(result),
            )

        except (TimeoutError, RuntimeError, ValueError) as e:
            return SubagentResult(
                task_id=subagent_id,
                success=False,
                result=f"Subagent failed: {e}",
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
        finally:
            if config.isolated_workspace:
                self._cleanup_workspace(work_dir)

    def _create_workspace(self, subagent_id: str, isolated: bool) -> Path:
        """Create workspace for subagent."""
        if isolated:
            # Create temp directory that gets cleaned up after the run.
            work_dir = Path(tempfile.mkdtemp(prefix=f"{self._temp_prefix}_{subagent_id}_"))
        else:
            # Use subdirectory in main workspace
            work_dir = self._subagent_dir / subagent_id
            work_dir.mkdir(exist_ok=True)

        return work_dir

    def _cleanup_workspace(self, work_dir: Path) -> None:
        """Remove an isolated workspace after the subagent completes."""
        try:
            shutil.rmtree(work_dir)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "Failed to remove isolated subagent workspace %s: %s",
                work_dir,
                exc,
            )

    def _build_subagent_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Build isolated system prompt for subagent."""
        context_str = json.dumps(context, indent=2, default=str)

        return f"""You are a subagent working in isolation.

TASK:
{task}

CONTEXT PROVIDED:
{context_str}

RULES:
1. You have limited iterations - be efficient
2. Return ONLY the final result, not your thinking process
3. If you need to use tools, they will be available
4. Focus on the task - do not deviate
5. Return your result in the final message

Your response will be passed back to the parent agent. Be concise and actionable.
"""

    def _run_subagent_loop(
        self,
        subagent_id: str,
        system_prompt: str,
        work_dir: Path,
        config: SubagentConfig,
    ) -> dict[str, Any]:
        """Run the subagent loop with fresh context."""

        # Fresh message context - KEY INSIGHT from learn-claude-code
        messages: list[dict[str, Any]] = [{"role": "user", "content": "Begin task."}]

        events: list[dict[str, Any]] = []
        iteration = 0
        deadline = time.monotonic() + max(float(config.timeout_seconds or 0), 0.01)

        while iteration < config.max_iterations:
            iteration += 1

            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                return {
                    "success": False,
                    "content": (f"Subagent timed out after {config.timeout_seconds}s at iteration {iteration - 1}"),
                    "events": events,
                    "iterations": iteration - 1,
                    "timed_out": True,
                }

            # Call LLM with fresh context
            response = self._call_llm_with_timeout(
                system_prompt=system_prompt,
                messages=messages,
                timeout_seconds=remaining_seconds,
            )

            # Extract content
            content_blocks = response.content
            tool_uses = [b for b in content_blocks if b.type == "tool_use"]
            text_blocks = [b for b in content_blocks if b.type == "text"]

            # Add assistant response to history
            messages.append({"role": "assistant", "content": content_blocks})

            # If no tools used, we're done
            if not tool_uses:
                final_text = "\n".join(t.text for t in text_blocks)
                return {
                    "success": True,
                    "content": final_text,
                    "events": events,
                    "iterations": iteration,
                }

            # Execute tools
            results = []
            for tool_block in tool_uses:
                if config.allowed_tools and tool_block.name not in config.allowed_tools:
                    result = f"Error: Tool '{tool_block.name}' not allowed"
                else:
                    handler = self.tool_handlers.get(tool_block.name)
                    if handler:
                        try:
                            result = handler(**tool_block.input, _work_dir=work_dir)
                        except (RuntimeError, ValueError) as e:
                            result = f"Error: {e}"
                    else:
                        result = f"Error: Unknown tool '{tool_block.name}'"

                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": str(result),
                    }
                )

                events.append(
                    {
                        "iteration": iteration,
                        "tool": tool_block.name,
                        "timestamp": time.time(),
                    }
                )

            messages.append({"role": "user", "content": results})

        # Max iterations reached
        return {
            "success": False,
            "content": f"Subagent reached max iterations ({config.max_iterations})",
            "events": events,
            "iterations": iteration,
        }

    def _call_llm_with_timeout(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        timeout_seconds: float,
    ) -> Any:
        """Call the underlying LLM client with an enforced wall-clock deadline."""
        if self.llm_client is None or not hasattr(self.llm_client, "messages"):
            raise RuntimeError("Subagent LLM client is not configured")
        if not hasattr(self.llm_client.messages, "create"):
            raise RuntimeError("Subagent LLM client does not expose messages.create")

        response_holder: dict[str, Any] = {}
        error_holder: dict[str, BaseException] = {}
        done = threading.Event()

        def _worker() -> None:
            try:
                response_holder["response"] = self.llm_client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=4000,
                    timeout=max(float(timeout_seconds), 0.01),
                )
            except (RuntimeError, ValueError) as exc:
                error_holder["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(
            target=_worker,
            name="kernelone-subagent-llm",
            daemon=True,
        )
        worker.start()

        if not done.wait(timeout=max(float(timeout_seconds), 0.01)):
            raise TimeoutError(f"Subagent LLM call timed out after {timeout_seconds:.1f}s")
        if "error" in error_holder:
            raise error_holder["error"]
        return response_holder["response"]

    def _estimate_pollution(self, result: dict[str, Any]) -> int:
        """Estimate how much this would have polluted parent context."""
        iterations = result.get("iterations", 1)
        tool_calls = len([e for e in result.get("events", []) if "tool" in e])

        # Rough estimate: each iteration with tools = ~500 tokens of pollution
        return iterations * 100 + tool_calls * 200


class ParallelSubagents:
    """Run multiple subagents in parallel."""

    def __init__(self, spawner: SubagentSpawner) -> None:
        self.spawner = spawner

    def map(
        self,
        tasks: list[dict[str, Any]],
        max_parallel: int = 3,
    ) -> list[SubagentResult]:
        """Map multiple tasks to subagents in parallel.

        Args:
            tasks: List of {task_description, context, config}
            max_parallel: Max concurrent subagents

        Returns:
            List of results in same order as tasks
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[SubagentResult | None] = [None] * len(tasks)

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            # Submit all tasks
            futures = {}
            for i, task in enumerate(tasks):
                future = executor.submit(
                    self.spawner.spawn,
                    task_description=task["description"],
                    context=task.get("context", {}),
                    config=task.get("config"),
                )
                futures[future] = i

            # Collect results
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except (RuntimeError, ValueError) as e:
                    results[idx] = SubagentResult(
                        task_id=f"parallel-{idx}",
                        success=False,
                        result=f"Parallel execution failed: {e}",
                    )

        # All None values should have been replaced with SubagentResult
        return results  # type: ignore[return-value]


class SubagentToolInterface:
    """Tool interface for subagent spawning."""

    def __init__(self, spawner: SubagentSpawner) -> None:
        self.spawner = spawner

    def spawn_subagent(
        self,
        task: str,
        context: dict[str, Any],
        max_iterations: int = 50,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Tool: Spawn a subagent for isolated task execution."""
        config = SubagentConfig(
            max_iterations=max_iterations,
            timeout_seconds=timeout,
        )

        result = self.spawner.spawn(task, context, config)

        return {
            "ok": result.success,
            "task_id": result.task_id,
            "result": result.result,
            "duration_ms": result.duration_ms,
            "context_saved": result.context_pollution_score,
        }

    def spawn_parallel(
        self,
        tasks: list[dict[str, str]],
        max_parallel: int = 3,
    ) -> dict[str, Any]:
        """Tool: Spawn multiple subagents in parallel."""
        parallel = ParallelSubagents(self.spawner)

        task_configs: list[dict[str, Any]] = [
            {
                "description": t["task"],
                "context": t.get("context", {}),
            }
            for t in tasks
        ]

        results = parallel.map(task_configs, max_parallel)

        return {
            "ok": all(r.success for r in results),
            "results": [
                {
                    "task_id": r.task_id,
                    "success": r.success,
                    "result": r.result,
                }
                for r in results
            ],
            "succeeded": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
        }
