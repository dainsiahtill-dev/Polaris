"""
Web tools: fetch and search web content.
"""
import json
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

# Try to import requests, fallback to urllib if not available
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .utils import error_result, find_repo_root

MAX_FETCH_BYTES = 256 * 1024  # 256KB max for fetched content
MAX_SEARCH_RESULTS = 20

# SSRF Protection: Blocked IP ranges and patterns
_BLOCKED_HOST_PATTERNS = [
    # Private IPv4 ranges
    r"^127\.",
    r"^10\.",
    r"^172\.(1[6-9]|2[0-9]|3[01])\.",
    r"^192\.168\.",
    r"^0\.",
    r"^169\.254\.",  # Link-local
    r"^224\.",  # Multicast
    r"^240\.",  # Reserved
    # IPv6
    r"^::1$",
    r"^fc00:",
    r"^fe80:",
    # Hostnames that indicate internal services
    r"localhost",
    r"\.internal[./]",
    r"\.local$",
    r"\.corp[./]",
    # Metadata services
    r"169\.254\.169\.254",  # AWS, GCP, Azure metadata
    r"metadata\.google\.internal",
    r"metadata\.azure\.internal",
]

_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "telnet", "ldap", "ldaps"}


def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Check if URL is safe to fetch (SSRF protection).

    Returns:
        (is_safe, reason_if_unsafe)
    """
    import urllib.parse
    import re

    if not url:
        return False, "Empty URL"

    # Parse URL
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return False, f"Invalid URL: {e}"

    # Block dangerous schemes
    scheme = parsed.scheme.lower()
    if scheme in _BLOCKED_SCHEMES:
        return False, f"URL scheme '{scheme}' is not allowed"

    if scheme not in ("http", "https"):
        return False, f"URL scheme '{scheme}' is not supported"

    # Extract hostname
    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    # Check blocked patterns
    hostname_lower = hostname.lower()
    for pattern in _BLOCKED_HOST_PATTERNS:
        if re.search(pattern, hostname_lower):
            return False, f"URL hostname matches blocked pattern: {pattern}"

    # Additional check for IP-based URLs to prevent DNS rebinding
    # Try to resolve and check the IP
    try:
        import socket
        ip = socket.getaddrinfo(hostname, None)[0][4][0]
        ip_lower = ip.lower()
        for pattern in _BLOCKED_HOST_PATTERNS:
            if re.search(pattern, ip_lower):
                return False, f"URL resolves to blocked IP: {ip}"
    except Exception:
        # If we can't resolve, let it through but it may fail later
        pass

    return True, ""


def _parse_common_args(args: List[str]) -> Dict[str, Any]:
    """Parse common arguments for web tools."""
    parsed: Dict[str, Any] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--max-chars", "-m") and i + 1 < len(args):
            try:
                parsed["max_chars"] = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if token in ("--max", "-n") and i + 1 < len(args):
            try:
                parsed["max_results"] = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if token.startswith("--") and i + 1 < len(args):
            # Generic --key value pair
            parsed[token[2:]] = args[i + 1]
            i += 2
            continue
        # Positional args
        if "query" not in parsed and "url" not in parsed:
            parsed["query"] = token
        elif "url" not in parsed:
            parsed["url"] = token
        i += 1
    return parsed


def web_fetch(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Fetch content from a URL.

    Usage: web_fetch --url <url> [--max-chars N]
           web_fetch <url>
    """
    _ = cwd
    parsed = _parse_common_args(args)

    url = parsed.get("url", "")
    if not url:
        return error_result("web_fetch", "Usage: web_fetch --url <url> [--max-chars N]")

    max_chars = parsed.get("max_chars", MAX_FETCH_BYTES)

    # Validate URL scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # SSRF Protection: Validate URL safety
    is_safe, reason = _is_safe_url(url)
    if not is_safe:
        return error_result("web_fetch", f"SSRF protection: {reason}", exit_code=1)

    start = time.time()

    try:
        if HAS_REQUESTS:
            response = requests.get(
                url,
                timeout=min(timeout, 30),
                headers={
                    "User-Agent": "Polaris/1.0 (web fetch tool)",
                    "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
                },
                allow_redirects=True,
            )
            response.raise_for_status()
            content = response.text
        else:
            # Fallback to urllib
            import urllib.request
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Polaris/1.0"}
            )
            with urllib.request.urlopen(req, timeout=min(timeout, 30)) as resp:
                content = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return error_result("web_fetch", f"Failed to fetch: {exc}", exit_code=1)

    # Truncate if needed
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    # Try to extract title from HTML
    title = ""
    if "<title>" in content.lower() or "<html>" in content.lower():
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

    # Strip HTML tags for plain text preview
    text_content = re.sub(r"<[^>]+>", "", content)
    text_content = re.sub(r"\s+", " ", text_content).strip()

    return {
        "ok": True,
        "tool": "web_fetch",
        "url": url,
        "title": title,
        "content": content,
        "text_preview": text_content[:500] + ("..." if len(text_content) > 500 else ""),
        "content_length": len(content),
        "truncated": truncated,
        "error": None,
        "exit_code": 0,
        "stdout": text_content[:1000],
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "truncated": truncated,
        "artifacts": [],
        "command": ["web_fetch", url],
    }


def web_search(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Search the web using Brave Search API (or fallback to DDG).

    Usage: web_search --query <query> [--max N]
           web_search <query>
    """
    _ = cwd
    parsed = _parse_common_args(args)

    query = parsed.get("query", "")
    if not query:
        return error_result("web_search", "Usage: web_search --query <query> [--max N]")

    max_results = parsed.get("max_results", MAX_SEARCH_RESULTS)
    max_results = min(max_results, 50)

    start = time.time()
    results: List[Dict[str, Any]] = []

    # Try Brave Search API first
    brave_api_key = os.environ.get("BRAVE_API_KEY")
    if brave_api_key:
        try:
            url = "https://api.brave.com/res/v1/web/search"
            headers = {"Accept": "application/json", "X-Subscription-Token": brave_api_key}
            params = {"q": query, "count": max_results}
            if HAS_REQUESTS:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
                data = response.json()
                for item in data.get("web", {}).get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                    })
        except Exception:
            pass

    # Fallback to DuckDuckGo HTML (no API key needed)
    if not results:
        try:
            ddg_url = "https://html.duckduckgo.com/html/"
            if HAS_REQUESTS:
                response = requests.post(
                    ddg_url,
                    data={"q": query, "b": max_results},
                    timeout=min(timeout, 15),
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                # Parse results from HTML
                for result in re.finditer(
                    r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>.*?'
                    r'<a class="result__snippet"[^>]*>([^<]+)</a>',
                    response.text,
                    re.DOTALL
                ):
                    if len(results) >= max_results:
                        break
                    results.append({
                        "title": result.group(2).strip(),
                        "url": result.group(1).strip(),
                        "description": result.group(3).strip(),
                    })
        except Exception as exc:
            pass

    # If still no results, return empty with message
    if not results:
        return {
            "ok": True,
            "tool": "web_search",
            "query": query,
            "results": [],
            "error": None,
            "exit_code": 0,
            "stdout": "(no results found)",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["web_search", query],
        }

    # Format results for output
    output_lines = [f"Search results for: {query}"]
    for i, r in enumerate(results, 1):
        output_lines.append(f"{i}. {r['title']}")
        output_lines.append(f"   {r['url']}")
        if r.get("description"):
            output_lines.append(f"   {r['description'][:200]}")
        output_lines.append("")

    return {
        "ok": True,
        "tool": "web_search",
        "query": query,
        "results": results,
        "count": len(results),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "truncated": len(results) >= max_results,
        "artifacts": [],
        "command": ["web_search", query],
    }
