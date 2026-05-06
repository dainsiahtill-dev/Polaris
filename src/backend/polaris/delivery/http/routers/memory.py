from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.schemas import MemoryDeleteResponse
from polaris.kernelone.memory.integration import get_memory_store, get_reflection_store, init_anthropomorphic_modules
from pydantic import BaseModel

from ._shared import StructuredHTTPException, require_auth

router = APIRouter(prefix="/memory", tags=["memory"])


class AnthroState(BaseModel):
    last_reflection_step: int
    recent_error_count: int
    total_memories: int
    total_reflections: int


@router.get("/state", dependencies=[Depends(require_auth)], response_model=AnthroState)  # DEPRECATED
async def get_state(request: Request) -> AnthroState:
    # Ensure modules initialized
    ramdisk_root = request.app.state.app_state.settings.ramdisk_root or "."
    init_anthropomorphic_modules(ramdisk_root)

    mem_store = get_memory_store()
    ref_store = get_reflection_store()

    if not mem_store:
        raise StructuredHTTPException(
            status_code=503, code="MEMORY_STORE_NOT_INITIALIZED", message="Memory store not initialized"
        )

    last_step = 0
    total_reflections = 0
    if ref_store:
        last_step = ref_store.get_last_reflection_step()
        total_reflections = len(ref_store.reflections)

    recent_errors = mem_store.count_recent_errors(last_step)
    total_memories = len(mem_store.memories)

    return AnthroState(
        last_reflection_step=last_step,
        recent_error_count=recent_errors,
        total_memories=total_memories,
        total_reflections=total_reflections,
    )


@router.get("/v2/state", dependencies=[Depends(require_auth)], response_model=AnthroState)
async def v2_get_state(request: Request) -> AnthroState:
    """Get anthropomorphic memory state (reflections and errors)."""
    ramdisk_root = request.app.state.app_state.settings.ramdisk_root or "."
    init_anthropomorphic_modules(ramdisk_root)

    mem_store = get_memory_store()
    ref_store = get_reflection_store()

    if not mem_store:
        raise StructuredHTTPException(
            status_code=503, code="MEMORY_STORE_NOT_INITIALIZED", message="Memory store not initialized"
        )

    last_step = 0
    total_reflections = 0
    if ref_store:
        last_step = ref_store.get_last_reflection_step()
        total_reflections = len(ref_store.reflections)

    recent_errors = mem_store.count_recent_errors(last_step)
    total_memories = len(mem_store.memories)

    return AnthroState(
        last_reflection_step=last_step,
        recent_error_count=recent_errors,
        total_memories=total_memories,
        total_reflections=total_reflections,
    )


@router.delete(
    "/memories/{memory_id}", dependencies=[Depends(require_auth)], response_model=MemoryDeleteResponse
)  # DEPRECATED
async def delete_memory(memory_id: str, request: Request) -> dict[str, str]:
    ramdisk_root = request.app.state.app_state.settings.ramdisk_root or "."
    init_anthropomorphic_modules(ramdisk_root)

    mem_store = get_memory_store()
    if not mem_store:
        raise StructuredHTTPException(
            status_code=503, code="MEMORY_STORE_NOT_INITIALIZED", message="Memory store not initialized"
        )

    success = mem_store.delete(memory_id)
    if not success:
        raise StructuredHTTPException(status_code=404, code="MEMORY_NOT_FOUND", message="Memory not found")

    return {"status": "deleted", "id": memory_id}


@router.delete("/v2/memories/{memory_id}", dependencies=[Depends(require_auth)], response_model=MemoryDeleteResponse)
async def v2_delete_memory(memory_id: str, request: Request) -> dict[str, str]:
    """Delete a memory entry by ID."""
    ramdisk_root = request.app.state.app_state.settings.ramdisk_root or "."
    init_anthropomorphic_modules(ramdisk_root)

    mem_store = get_memory_store()
    if not mem_store:
        raise StructuredHTTPException(
            status_code=503, code="MEMORY_STORE_NOT_INITIALIZED", message="Memory store not initialized"
        )

    success = mem_store.delete(memory_id)
    if not success:
        raise StructuredHTTPException(status_code=404, code="MEMORY_NOT_FOUND", message="Memory not found")

    return {"status": "deleted", "id": memory_id}
