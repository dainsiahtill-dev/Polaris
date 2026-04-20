from __future__ import annotations

from typing import Any

import requests
from polaris.kernelone.process.ollama_utils import KernelOllamaAdapter


class OllamaRuntimeAdapter(KernelOllamaAdapter):
    """Infrastructure adapter for Ollama HTTP calls."""

    def generate(
        self,
        *,
        prompt: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> dict[str, Any]:
        url = f"{str(host).rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": -1},
        }
        response = requests.post(
            url,
            json=payload,
            timeout=timeout_seconds if timeout_seconds > 0 else None,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def embed(
        self,
        *,
        text: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> list[float]:
        url = f"{str(host).rstrip('/')}/api/embeddings"
        payload = {
            "model": model,
            "prompt": text,
        }
        import requests
        response = requests.post(url, json=payload, timeout=timeout_seconds)
        
        if response.status_code == 404:
            # Fallback to newer Ollama /api/embed endpoint
            url = f"{str(host).rstrip('/')}/api/embed"
            payload = {
                "model": model,
                "input": text,
            }
            response = requests.post(url, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(embeddings, list) or len(embeddings) == 0:
                return []
            vector = embeddings[0]
            if not isinstance(vector, list):
                return []
            return [float(item) for item in vector if isinstance(item, (int, float))]

        response.raise_for_status()
        data = response.json()
        vector = data.get("embedding") if isinstance(data, dict) else None
        if not isinstance(vector, list):
            return []
        return [float(item) for item in vector if isinstance(item, (int, float))]


