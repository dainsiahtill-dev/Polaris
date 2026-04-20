from typing import Any

from fastapi import APIRouter, Depends
from polaris.cells.llm.control_plane.public import list_ollama_models, ollama_stop
from polaris.delivery.http.routers._shared import require_auth

router = APIRouter()


@router.get("/ollama/models", dependencies=[Depends(require_auth)])
def get_ollama_models() -> list[str]:
    return list_ollama_models()


@router.post("/ollama/stop", dependencies=[Depends(require_auth)])
def stop_ollama_models() -> dict[str, Any]:
    return ollama_stop()
