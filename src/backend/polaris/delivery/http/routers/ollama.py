from typing import Any

from fastapi import APIRouter, Depends
from polaris.cells.llm.control_plane.public import list_ollama_models, ollama_stop
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.schemas import OllamaModelsResponse, OllamaStopResponse

router = APIRouter()


@router.get("/ollama/models", dependencies=[Depends(require_auth)], response_model=OllamaModelsResponse)  # DEPRECATED
def get_ollama_models() -> dict[str, Any]:
    return {"models": list_ollama_models()}


@router.get("/v2/ollama/models", dependencies=[Depends(require_auth)], response_model=OllamaModelsResponse)
def v2_get_ollama_models() -> dict[str, Any]:
    """List available Ollama models."""
    return {"models": list_ollama_models()}


@router.post("/ollama/stop", dependencies=[Depends(require_auth)], response_model=OllamaStopResponse)  # DEPRECATED
def stop_ollama_models() -> dict[str, Any]:
    return ollama_stop()


@router.post("/v2/ollama/stop", dependencies=[Depends(require_auth)], response_model=OllamaStopResponse)
def v2_stop_ollama_models() -> dict[str, Any]:
    """Stop running Ollama models."""
    return ollama_stop()
