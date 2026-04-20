"""
Semantic code search using embeddings.
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .utils import error_result, find_repo_root, relpath

# Default index storage
INDEX_DIR = ".polaris/semantic_index"
INDEX_FILE = ".polaris/semantic_index/index.json"


def _get_embedding(text: str, model_name: str = "microsoft/codebert-base") -> Optional[List[float]]:
    """
    Get embeddings for text using available embedding models.
    Falls back to simple hash-based embeddings if no model available.
    """
    try:
        # Try sentence-transformers first
        from sentence_transformers import SentenceTransformer

        # Use a lightweight model suitable for code
        try:
            model = SentenceTransformer("microsoft/codebert-base")
        except Exception:
            try:
                model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            except Exception:
                model = SentenceTransformer("all-MiniLM-L6-v2")

        embedding = model.encode(text, show_progress_bar=False)
        return embedding.tolist()
    except ImportError:
        pass

    # Fallback: Simple hash-based pseudo-embedding
    # This is NOT real semantic search but provides basic functionality
    # Install sentence-transformers for real semantic search: pip install sentence-transformers
    import hashlib

    words = re.findall(r'\w+', text.lower())
    vec = [0.0] * 128

    for i, word in enumerate(words[:50]):
        hash_val = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
        vec[i % 128] += (hash_val % 100) / 100.0

    # Normalize
    magnitude = sum(x * x for x in vec) ** 0.5
    if magnitude > 0:
        vec = [x / magnitude for x in vec]

    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _extract_code_snippets(filepath: str, max_length: int = 500) -> List[Dict[str, Any]]:
    """Extract meaningful code snippets from a file."""
    snippets = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            lines = content.split("\n")

        # Extract functions
        func_pattern = r'^(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s]+)?:\s*(?:""".*?""")?'

        current_func = []
        current_name = None

        for i, line in enumerate(lines):
            # Check for function definition
            func_match = re.match(func_pattern, line.strip())

            if func_match:
                # Save previous function
                if current_func and current_name:
                    snippet_text = "\n".join(current_func)
                    if len(snippet_text) > 50:  # Skip trivial functions
                        snippets.append({
                            "type": "function",
                            "name": current_name,
                            "content": snippet_text[:max_length],
                            "line_start": i - len(current_func),
                        })

                current_func = [line]
                current_name = func_match.group(1)
            elif current_func:
                current_func.append(line)

                # Limit function size
                if len("\n".join(current_func)) > max_length:
                    # Save and start new
                    snippet_text = "\n".join(current_func)
                    snippets.append({
                        "type": "function",
                        "name": current_name,
                        "content": snippet_text[:max_length],
                        "line_start": i - len(current_func) + 1,
                    })
                    current_func = []
                    current_name = None

        # Don't forget the last function
        if current_func and current_name:
            snippet_text = "\n".join(current_func)
            if len(snippet_text) > 50:
                snippets.append({
                    "type": "function",
                    "name": current_name,
                    "content": snippet_text[:max_length],
                    "line_start": len(lines) - len(current_func),
                })

        # If no functions found, use the whole file as a snippet
        if not snippets and content.strip():
            snippets.append({
                "type": "file",
                "name": os.path.basename(filepath),
                "content": content[:max_length],
                "line_start": 1,
            })

    except Exception:
        pass

    return snippets


def semantic_index(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Build semantic index for the codebase.

    Usage: semantic_index [--dir <dir>] [--extensions .py,.js]
    """
    _ = timeout
    dir_arg = ""
    extensions = ".py"

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--dir", "-d") and i + 1 < len(args):
            dir_arg = args[i + 1]
            i += 2
            continue
        if token in ("--extensions", "-e") and i + 1 < len(args):
            extensions = args[i + 1]
            i += 2
            continue
        i += 1

    root = find_repo_root(cwd)
    index_dir = os.path.join(root, INDEX_DIR)
    os.makedirs(index_dir, exist_ok=True)

    # Parse extensions
    ext_list = [ext.strip() for ext in extensions.split(",")]

    # Scan files
    files_to_index = []
    for dirpath, _, filenames in os.walk(root):
        # Skip common non-code directories
        if any(skip in dirpath for skip in (".git", "node_modules", "__pycache__", ".venv", "venv", ".polaris")):
            continue
        for name in filenames:
            if any(name.endswith(ext) for ext in ext_list):
                files_to_index.append(os.path.join(dirpath, name))

    start = time.time()
    indexed_count = 0
    snippets_indexed = 0
    errors: List[str] = []

    # Build index
    index_data: Dict[str, Any] = {
        "version": "1.0",
        "root": root,
        "model": "sentence-transformers (or fallback)",
        "snippets": [],
    }

    for filepath in files_to_index:
        rel_path = relpath(root, filepath)

        try:
            snippets = _extract_code_snippets(filepath)

            for snippet in snippets:
                # Get embedding
                # Include function name in the text for better semantic matching
                search_text = f"{snippet.get('name', '')} {snippet.get('content', '')}"
                embedding = _get_embedding(search_text)

                if embedding:
                    index_data["snippets"].append({
                        "file": rel_path,
                        "type": snippet.get("type", "code"),
                        "name": snippet.get("name", ""),
                        "content": snippet.get("content", ""),
                        "line_start": snippet.get("line_start", 1),
                        "embedding": embedding,
                    })
                    snippets_indexed += 1

            indexed_count += 1

        except Exception as e:
            errors.append(f"{rel_path}: {str(e)}")

    # Save index
    index_path = os.path.join(root, INDEX_FILE)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False)

    duration = time.time() - start

    return {
        "ok": True,
        "tool": "semantic_index",
        "files_indexed": indexed_count,
        "snippets_indexed": snippets_indexed,
        "index_file": INDEX_FILE,
        "errors": errors[:10],
        "duration": duration,
        "error": None,
        "exit_code": 0,
        "stdout": f"Indexed {snippets_indexed} code snippets from {indexed_count} files in {duration:.2f}s",
        "stderr": "",
        "duration": duration,
        "duration_ms": int(duration * 1000),
        "truncated": False,
        "artifacts": [],
        "command": ["semantic_index"],
    }


def semantic_search(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Search code using semantic similarity.

    Usage: semantic_search --query <query> [--top N]
           semantic_search "找到处理用户支付失败重试的逻辑"
    """
    _ = timeout
    query = ""
    top_k = 5

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--query", "-q") and i + 1 < len(args):
            query = args[i + 1]
            i += 2
            continue
        if token in ("--top", "-n", "--limit") and i + 1 < len(args):
            try:
                top_k = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if not query:
            query = token
        i += 1

    if not query:
        return error_result("semantic_search", "Usage: semantic_search --query <query>")

    root = find_repo_root(cwd)
    index_path = os.path.join(root, INDEX_FILE)

    if not os.path.isfile(index_path):
        # Try to build index automatically
        build_result = semantic_index([], cwd, timeout)
        if not build_result.get("ok"):
            return error_result("semantic_search", "No index found. Run semantic_index first.")

    if not os.path.isfile(index_path):
        return error_result("semantic_search", "No index found. Run semantic_index first.")

    # Load index
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
    except Exception as exc:
        return error_result("semantic_search", f"Failed to load index: {exc}")

    snippets = index_data.get("snippets", [])
    if not snippets:
        return error_result("semantic_search", "Index is empty. Run semantic_index first.")

    # Get query embedding
    start = time.time()
    query_embedding = _get_embedding(query)

    if not query_embedding:
        return error_result("semantic_search", "Failed to compute embedding")

    # Calculate similarities
    results: List[Dict[str, Any]] = []

    for snippet in snippets:
        embedding = snippet.get("embedding", [])
        if embedding:
            similarity = _cosine_similarity(query_embedding, embedding)
            results.append({
                "file": snippet.get("file", ""),
                "type": snippet.get("type", "code"),
                "name": snippet.get("name", ""),
                "content": snippet.get("content", ""),
                "line_start": snippet.get("line_start", 1),
                "score": similarity,
            })

    # Sort by similarity
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_k]

    # Format output
    output_lines = [f"Semantic search: '{query}'", ""]
    output_lines.append(f"Top {len(results)} results:")
    output_lines.append("")

    for i, r in enumerate(results, 1):
        output_lines.append(f"{i}. {r['file']}:{r['line_start']} ({r['type']})")
        output_lines.append(f"   Name: {r['name']}")
        output_lines.append(f"   Score: {r['score']:.3f}")
        # Show first few lines of content
        content_preview = r["content"].split("\n")[:3]
        output_lines.append(f"   Preview: {' | '.join(content_preview[:2])}")
        output_lines.append("")

    duration = time.time() - start

    return {
        "ok": True,
        "tool": "semantic_search",
        "query": query,
        "results": results,
        "count": len(results),
        "duration": duration,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": duration,
        "duration_ms": int(duration * 1000),
        "truncated": False,
        "artifacts": [],
        "command": ["semantic_search", query],
    }


def semantic_clear(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Clear semantic index.

    Usage: semantic_clear
    """
    _ = args
    _ = timeout

    root = find_repo_root(cwd)
    index_path = os.path.join(root, INDEX_FILE)

    if not os.path.isfile(index_path):
        return {
            "ok": True,
            "tool": "semantic_clear",
            "message": "No index to clear",
            "error": None,
            "exit_code": 0,
            "stdout": "No index found",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["semantic_clear"],
        }

    try:
        os.unlink(index_path)
    except Exception as exc:
        return error_result("semantic_clear", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "semantic_clear",
        "message": "Index cleared",
        "error": None,
        "exit_code": 0,
        "stdout": "Semantic index cleared",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["semantic_clear"],
    }
