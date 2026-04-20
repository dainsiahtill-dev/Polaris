import contextvars
import os
import threading
from typing import Any, cast

import yaml
from polaris.kernelone.events.io_events import emit_event
from polaris.kernelone.storage import resolve_workspace_persistent_path

from .memory_store import MemoryStore
from .reflection import ReflectionGenerator, ReflectionScheduler, ReflectionStore
from .schema import MemoryItem, PromptContext

_MEMORY_STORES: dict[str, MemoryStore] = {}
_REFLECTION_STORES: dict[str, ReflectionStore] = {}
_REFLECTION_SCHEDULERS: dict[str, ReflectionScheduler] = {}
_PERSONA_CONFIGS: dict[str, dict[str, Any]] = {}
_ACTIVE_WORKSPACE_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "kernelone_memory_workspace_key",
    default="",
)
_STORE_LOCK = threading.RLock()


def _workspace_key(project_root: str) -> str:
    return os.path.abspath(str(project_root or os.getcwd()))


def _resolve_active_key(project_root: str | None = None) -> str:
    if project_root:
        return _workspace_key(project_root)
    active = str(_ACTIVE_WORKSPACE_KEY.get() or "").strip()
    if active:
        return active
    with _STORE_LOCK:
        if len(_MEMORY_STORES) == 1:
            return next(iter(_MEMORY_STORES))
    return ""


def get_brain_path(base_dir: str, filename: str) -> str:
    """Returns path to brain files (memory/reflection jsonl)."""
    return resolve_workspace_persistent_path(base_dir, f"workspace/brain/{filename}")


def get_memory_store(project_root: str | None = None) -> MemoryStore | None:
    key = _resolve_active_key(project_root)
    if not key:
        return None
    with _STORE_LOCK:
        return _MEMORY_STORES.get(key)


def get_reflection_store(project_root: str | None = None) -> ReflectionStore | None:
    key = _resolve_active_key(project_root)
    if not key:
        return None
    with _STORE_LOCK:
        return _REFLECTION_STORES.get(key)


def get_role_persona_config(
    role: str | None = None,
    *,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Return persona config for a role or the whole roles map.

    This keeps the memory package API stable for callers within Polaris.
    """
    key = _resolve_active_key(project_root)
    with _STORE_LOCK:
        config = _PERSONA_CONFIGS.get(key, {})
    # Explicit type narrowing for mypy
    roles_raw = config.get("roles")
    roles: dict[str, Any] = roles_raw if isinstance(roles_raw, dict) else {}
    if role is None:
        return dict(roles)
    role_key = str(role or "").strip().lower()
    role_config = roles.get(role_key)
    if isinstance(role_config, dict):
        return dict(role_config)
    return {}


def _get_persona_config(project_root: str | None = None) -> dict[str, Any]:
    key = _resolve_active_key(project_root)
    with _STORE_LOCK:
        config = _PERSONA_CONFIGS.get(key, {})
    return dict(config) if isinstance(config, dict) else {}


def init_anthropomorphic_modules(project_root: str) -> None:
    key = _workspace_key(project_root)
    _ACTIVE_WORKSPACE_KEY.set(key)
    with _STORE_LOCK:
        if key not in _MEMORY_STORES:
            mem_file = get_brain_path(key, "MEMORY.jsonl")
            _MEMORY_STORES[key] = MemoryStore(mem_file)

        if key not in _REFLECTION_STORES:
            ref_file = get_brain_path(key, "REFLECTIONS.jsonl")
            _REFLECTION_STORES[key] = ReflectionStore(ref_file)

        if key not in _REFLECTION_SCHEDULERS:
            _REFLECTION_SCHEDULERS[key] = ReflectionScheduler()

        if key not in _PERSONA_CONFIGS:
            persona_path = os.path.join(key, "prompts", "role_persona.yaml")
            if os.path.exists(persona_path):
                with open(persona_path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                _PERSONA_CONFIGS[key] = (
                    loaded if isinstance(loaded, dict) else {"feature_flags": {"anthro_enabled": False}}
                )
            else:
                _PERSONA_CONFIGS[key] = {"feature_flags": {"anthro_enabled": False}}


def get_persona_text(role: str, *, project_root: str | None = None) -> str:
    key = _resolve_active_key(project_root)
    with _STORE_LOCK:
        config = _PERSONA_CONFIGS.get(key, {})
    if not config or not config.get("feature_flags", {}).get("anthro_enabled", False):
        return ""

    role_key = role.lower()
    role_data = config.get("roles", {}).get(role_key)
    if not role_data:
        return ""

    style = role_data.get("style", "")
    quirks = "\n- ".join(role_data.get("quirks", []))
    taboos = "\n- ".join(role_data.get("taboo", []))

    return f"""
PERSONALITY INJECTION:
You are acting as the {role.upper()}.
Style: {style}
Quirks:
- {quirks}
Taboos (NEVER do this):
- {taboos}
""".strip()


def get_anthropomorphic_context(
    project_root: str, role: str, query: str, step: int, run_id: str, phase: str
) -> dict[str, Any]:
    """
    Retrieves Persona and Memories for prompt injection.
    Returns a dict with contents and a PromptContext structure.
    """
    init_anthropomorphic_modules(project_root)

    # 1. Persona
    persona_text = get_persona_text(role, project_root=project_root)
    mem_store = get_memory_store(project_root)
    ref_store = get_reflection_store(project_root)
    persona_cfg = _get_persona_config(project_root)

    # 2. Retrieval
    retrieved_memories: list[MemoryItem] = []
    retrieved_reflections: list[Any] = []
    retrieved_scores: list[float] = []

    if persona_cfg and persona_cfg.get("feature_flags", {}).get("anthro_enabled", False):
        # Retrieve Memories
        # Query usually comes from the plan or current objective
        # Retrieve Memories with scores
        # Query usually comes from the plan or current objective
        if mem_store is None:
            return {
                "persona_instruction": persona_text,
                "anthropomorphic_context": "",
                "prompt_context_obj": PromptContext(
                    run_id=run_id,
                    phase=phase,
                    step=step,
                    persona_id=f"{role}.v1",
                    retrieved_mem_ids=[],
                    retrieved_mem_scores=[],
                    retrieved_ref_ids=[],
                    token_usage_estimate=int(len(persona_text) / 4),
                ),
            }
        retrieved_results: list[tuple[MemoryItem, float]] | list[MemoryItem] = mem_store.retrieve(
            query=query, current_step=step, top_k=10, return_scores=True
        )
        # Unpack with type narrowing - retrieve() returns list[tuple] when return_scores=True
        if retrieved_results and isinstance(retrieved_results[0], tuple):
            # Type narrowing: retrieved_results is list[tuple[MemoryItem, float]]
            tuple_results = retrieved_results  # type: ignore[assignment]
            retrieved_memories = [item for item, _score in tuple_results]  # type: ignore[misc]
            retrieved_scores = [score for _item, score in tuple_results]  # type: ignore[misc]
        elif retrieved_results:
            # Type narrowing: retrieved_results is list[MemoryItem] when not tuples
            retrieved_memories = cast("list[MemoryItem]", retrieved_results)
            retrieved_scores = []

        # Retrieve active Reflections
        if ref_store:
            retrieved_reflections = ref_store.retrieve_active(current_step=step)
            # Todo: filtering reflections by relevance if needed

    # 3. Format Memory Block
    # Token Budget: Max 10 items, < 200 chars each (soft enforcement via truncation)
    mem_block_lines = []
    if retrieved_memories or retrieved_reflections:
        mem_block_lines.append("## RELEVANT MEMORIES & INSIGHTS (Retrieval)")

        if retrieved_reflections:
            mem_block_lines.append("### Strategic Insights (Reflections):")
            for ref in retrieved_reflections[:3]:  # Max 3 reflections
                if hasattr(ref, "text") and hasattr(ref, "scope"):
                    text = ref.text[:240] + "..." if len(ref.text) > 240 else ref.text
                    mem_block_lines.append(f"- [Scope: {','.join(ref.scope)}] {text}")

        if retrieved_memories:
            mem_block_lines.append("### Past Experiences:")
            for mem in retrieved_memories:
                if hasattr(mem, "step") and hasattr(mem, "text") and hasattr(mem, "kind"):
                    delta = step - mem.step
                    ago = f"{delta} steps ago" if delta > 0 else "Just now"
                    text = mem.text[:200] + "..." if len(mem.text) > 200 else mem.text
                    mem_block_lines.append(f"- [{mem.kind.upper()} | {ago}] {text}")

    memory_text = "\n".join(mem_block_lines)

    # 4. Construct PromptContext for event log
    prompt_context = PromptContext(
        run_id=run_id,
        phase=phase,
        step=step,
        persona_id=f"{role}.v1",
        retrieved_mem_ids=[m.id for m in retrieved_memories if hasattr(m, "id")],
        retrieved_mem_scores=retrieved_scores if "retrieved_scores" in locals() else [],
        retrieved_ref_ids=[r.id for r in retrieved_reflections if hasattr(r, "id")],
        token_usage_estimate=int(len(persona_text) / 4 + len(memory_text) / 4),  # Rough estimate
    )

    return {
        "persona_instruction": persona_text,
        "anthropomorphic_context": memory_text,
        "prompt_context_obj": prompt_context,
    }


def run_reflection_cycle(project_root: str, current_step: int, run_id: str, model: str, events_path: str = "") -> None:
    """
    Checks if reflection is due and runs generation if so.
    """
    init_anthropomorphic_modules(project_root)
    mem_store = get_memory_store(project_root)
    ref_store = get_reflection_store(project_root)
    key = _resolve_active_key(project_root)
    with _STORE_LOCK:
        scheduler = _REFLECTION_SCHEDULERS.get(key)
        persona_cfg = _PERSONA_CONFIGS.get(key, {})

    if not persona_cfg or not persona_cfg.get("feature_flags", {}).get("anthro_enabled", False):
        return

    # 1. Check Schedule
    if ref_store is None or mem_store is None or scheduler is None:
        return
    last_step = ref_store.get_last_reflection_step()
    recent_errors = mem_store.count_recent_errors(last_step)

    if not scheduler.should_reflect(current_step, last_step, recent_errors):
        return

    # 2. Prepare Data
    memories = mem_store.retrieve_recent(last_step)
    if not memories:
        return

    # 3. Generate
    generator = ReflectionGenerator(model, project_root)
    reflections = generator.generate(memories, current_step)

    if not reflections:
        return

    # 4. Store
    for ref in reflections:
        ref_store.append(ref)

    # 5. Emit Event
    if events_path:
        emit_event(
            events_path,
            kind="observation",
            actor="System",
            name="reflection",
            refs={"run_id": run_id, "step": current_step},
            summary=f"Generated {len(reflections)} insights",
            output={"reflections": [r.model_dump() for r in reflections]},
        )
