"""Architect role agent implementation.

This agent is intentionally lightweight and side-effect bounded:
- no direct file I/O
- no direct subprocess calls
- all state kept in-memory within the running process
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any

from polaris.cells.roles.runtime.public.service import (
    AgentMessage,
    MessageType,
    RoleAgent,
)


@dataclass
class BlueprintRecord:
    """In-memory architecture blueprint record."""

    blueprint_id: str
    task_id: str
    title: str
    modules: list[str] = field(default_factory=list)
    file_structure: dict[str, Any] = field(default_factory=dict)
    contracts: dict[str, str] = field(default_factory=dict)
    boundaries: dict[str, str] = field(default_factory=dict)
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "task_id": self.task_id,
            "title": self.title,
            "modules": list(self.modules),
            "file_structure": dict(self.file_structure),
            "contracts": dict(self.contracts),
            "boundaries": dict(self.boundaries),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class BlueprintStore:
    """Thread-safe in-memory store for architect blueprints."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_id: dict[str, BlueprintRecord] = {}

    def save(self, record: BlueprintRecord) -> None:
        with self._lock:
            record.updated_at = datetime.now().isoformat()
            self._by_id[record.blueprint_id] = record

    def get(self, blueprint_id: str) -> BlueprintRecord | None:
        with self._lock:
            return self._by_id.get(str(blueprint_id or "").strip())

    def list_all(self) -> list[BlueprintRecord]:
        with self._lock:
            rows = list(self._by_id.values())
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)

    def list_by_task(self, task_id: str) -> list[BlueprintRecord]:
        token = str(task_id or "").strip()
        with self._lock:
            rows = [item for item in self._by_id.values() if item.task_id == token]
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)


class ArchitectAgent(RoleAgent):
    """Architect role agent (`Architect`) on top of RoleAgent runtime."""

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, agent_name="Architect")
        self._store = BlueprintStore()

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register(
            "create_blueprint",
            self._tool_create_blueprint,
            description="Create architecture blueprint",
            parameters={"task_id": "Task ID", "title": "Blueprint title", "modules": "Optional module list"},
        )
        tb.register(
            "update_blueprint_structure",
            self._tool_update_blueprint_structure,
            description="Update blueprint file structure",
            parameters={"blueprint_id": "Blueprint ID", "file_structure": "Object mapping"},
        )
        tb.register(
            "add_contract",
            self._tool_add_contract,
            description="Add a contract definition to blueprint",
            parameters={"blueprint_id": "Blueprint ID", "contract_name": "Name", "contract_definition": "Definition"},
        )
        tb.register(
            "add_boundary",
            self._tool_add_boundary,
            description="Add a boundary definition to blueprint",
            parameters={"blueprint_id": "Blueprint ID", "boundary_name": "Name", "boundary_definition": "Definition"},
        )
        tb.register(
            "finalize_blueprint",
            self._tool_finalize_blueprint,
            description="Finalize blueprint",
            parameters={"blueprint_id": "Blueprint ID"},
        )
        tb.register(
            "get_blueprint",
            self._tool_get_blueprint,
            description="Get blueprint details",
            parameters={"blueprint_id": "Blueprint ID"},
        )
        tb.register(
            "list_blueprints",
            self._tool_list_blueprints,
            description="List blueprints",
            parameters={"task_id": "Optional task id"},
        )

    def _next_blueprint_id(self, task_id: str) -> str:
        token = str(task_id or "task").strip().replace(" ", "_")
        return f"bp_{token}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    def _tool_create_blueprint(
        self,
        task_id: str,
        title: str,
        modules: list[str] | None = None,
    ) -> dict[str, Any]:
        record = BlueprintRecord(
            blueprint_id=self._next_blueprint_id(task_id),
            task_id=str(task_id or "").strip(),
            title=str(title or "Architecture Blueprint").strip() or "Architecture Blueprint",
            modules=[str(item).strip() for item in list(modules or []) if str(item).strip()],
        )
        self._store.save(record)
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_update_blueprint_structure(
        self,
        blueprint_id: str,
        file_structure: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._store.get(blueprint_id)
        if record is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        record.file_structure = dict(file_structure or {})
        self._store.save(record)
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_add_contract(
        self,
        blueprint_id: str,
        contract_name: str,
        contract_definition: str,
    ) -> dict[str, Any]:
        record = self._store.get(blueprint_id)
        if record is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        name = str(contract_name or "").strip()
        if not name:
            return {"ok": False, "error": "contract_name_required"}
        record.contracts[name] = str(contract_definition or "").strip()
        self._store.save(record)
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_add_boundary(
        self,
        blueprint_id: str,
        boundary_name: str,
        boundary_definition: str,
    ) -> dict[str, Any]:
        record = self._store.get(blueprint_id)
        if record is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        name = str(boundary_name or "").strip()
        if not name:
            return {"ok": False, "error": "boundary_name_required"}
        record.boundaries[name] = str(boundary_definition or "").strip()
        self._store.save(record)
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_finalize_blueprint(self, blueprint_id: str) -> dict[str, Any]:
        record = self._store.get(blueprint_id)
        if record is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        record.status = "finalized"
        self._store.save(record)
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_get_blueprint(self, blueprint_id: str) -> dict[str, Any]:
        record = self._store.get(blueprint_id)
        if record is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        return {"ok": True, "blueprint": record.to_dict()}

    def _tool_list_blueprints(self, task_id: str | None = None) -> dict[str, Any]:
        rows = self._store.list_by_task(task_id or "") if str(task_id or "").strip() else self._store.list_all()  # type: ignore[arg-type]
        return {"ok": True, "count": len(rows), "blueprints": [item.to_dict() for item in rows]}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        payload = dict(message.payload or {})
        if message.type != MessageType.TASK:
            return None
        action = str(payload.get("action") or "").strip().lower()
        if action == "create_blueprint":
            result = self._tool_create_blueprint(
                task_id=str(payload.get("task_id") or "").strip(),
                title=str(payload.get("title") or "Architecture Blueprint").strip(),
                modules=payload.get("modules") if isinstance(payload.get("modules"), list) else [],
            )
        elif action == "update_blueprint_structure":
            result = self._tool_update_blueprint_structure(
                blueprint_id=str(payload.get("blueprint_id") or "").strip(),
                file_structure=payload.get("file_structure") if isinstance(payload.get("file_structure"), dict) else {},  # type: ignore[arg-type]
            )
        elif action == "finalize_blueprint":
            result = self._tool_finalize_blueprint(
                blueprint_id=str(payload.get("blueprint_id") or "").strip(),
            )
        elif action == "get_blueprint":
            result = self._tool_get_blueprint(
                blueprint_id=str(payload.get("blueprint_id") or "").strip(),
            )
        elif action == "list_blueprints":
            result = self._tool_list_blueprints(
                task_id=str(payload.get("task_id") or "").strip() or None,
            )
        else:
            result = {"ok": False, "error": "unsupported_action", "action": action}

        return AgentMessage.create(
            msg_type=MessageType.RESULT,
            sender=self.agent_name,
            receiver=message.sender,
            payload={"role": "architect", "action": action, "result": result},
            correlation_id=message.id,
        )

    def run_cycle(self) -> bool:
        message = self.message_queue.receive(block=False)
        if message is None:
            return False
        response = self.handle_message(message)
        if response is not None:
            self.message_queue.send(response)
        return True
