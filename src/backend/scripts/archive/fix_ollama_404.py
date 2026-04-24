import re
from pathlib import Path

# 1. Fix Ollama embedding adapter to fallback to /api/embed
adapter_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/infrastructure/llm/adapters/ollama_runtime_adapter.py')
content = adapter_path.read_text(encoding='utf-8')

new_embed_method = '''    def embed(
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
        return [float(item) for item in vector if isinstance(item, (int, float))]'''

content = re.sub(r'    def embed\([^)]+\)\s*->\s*list\[float\]:.*?(?=    def|$)', new_embed_method + '\n\n', content, flags=re.DOTALL)
adapter_path.write_text(content, encoding='utf-8')


# 2. Fix TruthLogService unretrieved exception
tl_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone/context/truth_log_service.py')
tl_content = tl_path.read_text(encoding='utf-8')

new_callback = '''
            def _handle_index_error(t):
                try:
                    t.result()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Truth log background indexing failed (safe to ignore): {e}")

            task.add_done_callback(_handle_index_error)
'''

tl_content = tl_content.replace('task.add_done_callback(lambda _: None)  # Suppress unused warning', new_callback.strip())
tl_path.write_text(tl_content, encoding='utf-8')
print("Applied fixes for Ollama embeddings and asyncio unretrieved exceptions")
