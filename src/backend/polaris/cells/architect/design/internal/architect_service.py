"""Architect Service (中书令) - Architecture design as a Service.

This module implements the Architect role as a proper service in the Polaris v2 architecture.
Responsible for creating project documentation, ADRs, and interface contracts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

logger = logging.getLogger(__name__)


@dataclass
class ArchitectConfig:
    """Configuration for Architect Service."""

    workspace: str
    docs_dir: str = "docs/product"


@dataclass
class ArchitectureDoc:
    """An architecture document."""

    doc_id: str
    doc_type: str  # requirements, adr, interface_contract, plan
    title: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "1.0"


class ArchitectService:
    """Architect Service - creates project documentation and specifications.

    Responsibilities:
    - Create requirements documents
    - Write Architecture Decision Records (ADRs)
    - Define interface contracts
    - Create implementation plans
    """

    def __init__(
        self,
        config: ArchitectConfig,
        message_bus: MessageBus | None = None,
    ) -> None:
        self.config = config
        self._bus = message_bus or MessageBus()
        self._docs: dict[str, ArchitectureDoc] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start Architect Service."""
        self._running = True
        self._task = asyncio.create_task(self._main_loop())

        # Subscribe to relevant messages
        await self._bus.subscribe(MessageType.PLAN_CREATED, self._on_plan_created)

        logger.info("[Architect Service] Started for workspace: %s", self.config.workspace)

    async def stop(self) -> None:
        """Stop Architect Service."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        # Unsubscribe from message bus to prevent memory leaks
        await self._bus.unsubscribe(MessageType.PLAN_CREATED, self._on_plan_created)

        logger.info("[Architect Service] Stopped")

    async def create_requirements_doc(
        self,
        goal: str,
        in_scope: list[str],
        out_of_scope: list[str],
        constraints: list[str],
        definition_of_done: list[str],
        backlog: list[str],
    ) -> ArchitectureDoc:
        """Create requirements document."""
        logger.info("[Architect Service] Creating requirements doc for: %s", goal)

        # Generate content using LLM
        content = await self._generate_requirements_content(
            goal, in_scope, out_of_scope, constraints, definition_of_done, backlog
        )

        doc = ArchitectureDoc(
            doc_id=f"req-{len(self._docs) + 1}",
            doc_type="requirements",
            title=f"Requirements: {goal}",
            content=content,
        )
        self._docs[doc.doc_id] = doc

        # Write to file
        await self._write_doc_to_file(doc, "requirements.md")

        return doc

    async def create_adr(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: list[str],
    ) -> ArchitectureDoc:
        """Create Architecture Decision Record."""
        logger.info("[Architect Service] Creating ADR: %s", title)

        content = f"""# ADR: {title}

## Context

{context}

## Decision

{decision}

## Consequences

{chr(10).join(f"- {c}" for c in consequences)}
"""

        doc = ArchitectureDoc(
            doc_id=f"adr-{len(self._docs) + 1}",
            doc_type="adr",
            title=title,
            content=content,
        )
        self._docs[doc.doc_id] = doc

        await self._write_doc_to_file(doc, "adr.md")

        return doc

    async def create_interface_contract(
        self,
        api_name: str,
        endpoints: list[dict],
    ) -> ArchitectureDoc:
        """Create interface contract/API specification."""
        logger.info("[Architect Service] Creating interface contract for: %s", api_name)

        content = f"""# Interface Contract: {api_name}

## Endpoints

"""
        for endpoint in endpoints:
            content += f"""
### {endpoint.get("method", "GET")} {endpoint.get("path", "/")}

{endpoint.get("description", "")}

**Request:**
```json
{json.dumps(endpoint.get("request", {}), indent=2)}
```

**Response:**
```json
{json.dumps(endpoint.get("response", {}), indent=2)}
```
"""

        doc = ArchitectureDoc(
            doc_id=f"contract-{len(self._docs) + 1}",
            doc_type="interface_contract",
            title=f"Interface: {api_name}",
            content=content,
        )
        self._docs[doc.doc_id] = doc

        await self._write_doc_to_file(doc, "interface_contract.md")

        return doc

    async def create_implementation_plan(
        self,
        milestones: list[str],
        verification_commands: list[str],
        risks: list[dict],
    ) -> ArchitectureDoc:
        """Create implementation plan."""
        logger.info("[Architect Service] Creating implementation plan")

        content = "# Implementation Plan\n\n## Delivery Milestones\n\n"
        for i, milestone in enumerate(milestones, 1):
            content += f"{i}. {milestone}\n"

        content += "\n## Verification Commands\n\n"
        for cmd in verification_commands:
            content += f"- `{cmd}`\n"

        content += "\n## Risks and Mitigations\n\n"
        for risk in risks:
            content += f"- **{risk.get('risk', 'Unknown')}**: {risk.get('mitigation', 'TBD')}\n"

        doc = ArchitectureDoc(
            doc_id=f"plan-{len(self._docs) + 1}",
            doc_type="plan",
            title="Implementation Plan",
            content=content,
        )
        self._docs[doc.doc_id] = doc

        await self._write_doc_to_file(doc, "plan.md")

        return doc

    async def _generate_requirements_content(
        self,
        goal: str,
        in_scope: list[str],
        out_of_scope: list[str],
        constraints: list[str],
        definition_of_done: list[str],
        backlog: list[str],
    ) -> str:
        """Generate requirements document content using LLM."""
        prompt = f"""You are an architect creating a requirements document.

Goal: {goal}

In Scope:
{chr(10).join(f"- {item}" for item in in_scope)}

Out of Scope:
{chr(10).join(f"- {item}" for item in out_of_scope)}

Constraints:
{chr(10).join(f"- {item}" for item in constraints)}

Definition of Done:
{chr(10).join(f"- {item}" for item in definition_of_done)}

Backlog:
{chr(10).join(f"- {item}" for item in backlog)}

Generate a well-structured requirements document in Markdown format.
Include sections for: Goal, Scope, Constraints, Acceptance Criteria, and Backlog.
"""

        try:
            from polaris.kernelone.process.ollama_utils import invoke_ollama

            model = os.environ.get("KERNELONE_ARCHITECT_MODEL", "MiniMax-M2.5")

            # 使用 asyncio.to_thread() 包装同步调用，避免阻塞事件循环
            # 注意: asyncio.to_thread 只接受位置参数，关键字参数需通过 functools.partial 传递
            import functools

            bound_invoke = functools.partial(
                invoke_ollama,
                prompt,
                model,
                self.config.workspace,  # workspace (not workspace_path)
                False,  # show_output
                120,  # timeout
            )
            response = await asyncio.to_thread(bound_invoke)

            return str(response)

        except (RuntimeError, ValueError) as e:
            logger.error("[Architect Service] Error generating content: %s", e)

            # Fallback: return structured content
            return f"""# Requirements: {goal}

## Goal

{goal}

## In Scope

{chr(10).join(f"- {item}" for item in in_scope)}

## Out of Scope

{chr(10).join(f"- {item}" for item in out_of_scope)}

## Constraints

{chr(10).join(f"- {item}" for item in constraints)}

## Definition of Done

{chr(10).join(f"- {item}" for item in definition_of_done)}

## Backlog

{chr(10).join(f"- {item}" for item in backlog)}
"""

    async def _write_doc_to_file(self, doc: ArchitectureDoc, filename: str) -> None:
        """Write document to file in workspace.

        Args:
            doc: The architecture document to write.
            filename: The target filename (e.g., "requirements.md").
        """
        docs_dir = Path(self.config.workspace) / self.config.docs_dir

        filepath = docs_dir / filename

        # Use asyncio.to_thread to avoid blocking the event loop during file I/O
        await asyncio.to_thread(
            self._sync_write_file,
            self.config.workspace,
            filepath,
            doc.content,
        )

        logger.info("[Architect Service] Wrote %s to %s", filename, docs_dir)

    @staticmethod
    def _sync_write_file(workspace: str, filepath: Path, content: str) -> None:
        """Synchronous file write helper.

        Args:
            workspace: Workspace root for KernelFileSystem.
            filepath: The path to write to.
            content: The content to write.
        """
        fs = KernelFileSystem(workspace, get_default_adapter())
        fs.workspace_write_text(str(filepath), str(content or ""), encoding="utf-8")

    async def _main_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as e:
                logger.error("[Architect Service] Error in main loop: %s", e)

    async def _on_plan_created(self, message: Message) -> None:
        """Handle plan creation notifications."""
        logger.info("[Architect Service] Plan created: %s", message.payload.get("subject"))

    def get_status(self) -> dict[str, Any]:
        """Get Architect Service status."""
        return {
            "running": self._running,
            "workspace": self.config.workspace,
            "docs": len(self._docs),
            "doc_ids": list(self._docs.keys()),
        }
