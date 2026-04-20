"""
MCP Client tools: interact with MCP servers with retry and connection pooling.
"""
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Callable
from functools import wraps

from .utils import error_result

# Configure logging
logger = logging.getLogger(__name__)

# MCP server configurations (can be loaded from config)
MCP_SERVERS: Dict[str, Dict[str, Any]] = {}

# HTTP connection pool cache
_connection_pools: Dict[str, Any] = {}

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_BACKOFF_FACTOR = 2.0


def _retry_with_backoff(
    max_retries: int = DEFAULT_MAX_RETRIES,
    delay: float = DEFAULT_RETRY_DELAY,
    backoff: float = DEFAULT_BACKOFF_FACTOR,
    exceptions: tuple = (Exception,)
):
    """Decorator for retrying functions with exponential backoff."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"{func.__name__} failed after {max_retries} attempts: {e}")

            raise last_exception if last_exception else RuntimeError("Unknown error")
        return wrapper
    return decorator


def _load_mcp_config() -> Dict[str, Dict[str, Any]]:
    """Load MCP server configurations from environment or config file."""
    global MCP_SERVERS

    if MCP_SERVERS:
        return MCP_SERVERS

    # Try to load from config file
    config_paths = [
        os.path.expanduser("~/.polaris/mcp_servers.json"),
        os.path.join(os.getcwd(), ".polaris", "mcp_servers.json"),
    ]

    for config_path in config_paths:
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    MCP_SERVERS = json.load(f)
                    logger.debug(f"Loaded MCP config from {config_path}")
                return MCP_SERVERS
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in {config_path}: {e}")
            except Exception as e:
                logger.warning(f"Failed to load config from {config_path}: {e}")

    # Try environment variable for quick config
    mcp_env = os.environ.get("POLARIS_MCP_SERVERS")
    if mcp_env:
        try:
            MCP_SERVERS = json.loads(mcp_env)
            logger.debug("Loaded MCP config from environment variable")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in POLARIS_MCP_SERVERS: {e}")

    return MCP_SERVERS


def _validate_server_config(name: str, config: Dict[str, Any]) -> Optional[str]:
    """Validate MCP server configuration.

    Returns error message if invalid, None if valid.
    """
    if not isinstance(config, dict):
        return f"Server '{name}': configuration must be an object"

    server_type = config.get("type", "stdio")

    if server_type not in ("stdio", "http"):
        return f"Server '{name}': unsupported type '{server_type}'"

    if server_type == "stdio":
        command = config.get("command", "")
        if not command:
            return f"Server '{name}': stdio servers require a 'command'"
    elif server_type == "http":
        url = config.get("url", "")
        if not url:
            return f"Server '{name}': http servers require a 'url'"
        if not url.startswith(("http://", "https://")):
            return f"Server '{name}': url must start with http:// or https://"

    return None


def _get_http_session(server_config: Dict[str, Any]) -> Any:
    """Get or create a requests session with connection pooling."""
    import requests
    from urllib.parse import urlparse

    url = server_config.get("url", "")
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    if base_url not in _connection_pools:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=3
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _connection_pools[base_url] = session
        logger.debug(f"Created HTTP connection pool for {base_url}")

    return _connection_pools[base_url]


def mcp_validate_config(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Validate MCP server configurations.

    Usage: mcp_validate_config
    """
    _ = args
    _ = cwd
    _ = timeout

    servers = _load_mcp_config()
    errors = []
    warnings_list = []
    valid_servers = []

    for name, config in servers.items():
        error = _validate_server_config(name, config)
        if error:
            errors.append(error)
        else:
            # Check for recommended fields
            if not config.get("description"):
                warnings_list.append(f"Server '{name}': missing 'description' field")
            if config.get("type") == "stdio" and not config.get("env"):
                warnings_list.append(f"Server '{name}': consider adding 'env' for environment variables")

            valid_servers.append(name)

    return {
        "ok": len(errors) == 0,
        "tool": "mcp_validate_config",
        "valid_servers": valid_servers,
        "invalid_count": len(errors),
        "errors": errors,
        "warnings": warnings_list,
        "error": "\n".join(errors) if errors else None,
        "exit_code": 0 if not errors else 1,
        "stdout": f"Validated {len(servers)} server(s): {len(valid_servers)} valid, {len(errors)} invalid" +
                  (f"\nWarnings:\n" + "\n".join(f"  ! {w}" for w in warnings_list) if warnings_list else ""),
        "stderr": "\n".join(errors) if errors else "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["mcp_validate_config"],
    }


def mcp_list_servers(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    List available MCP servers.

    Usage: mcp_list_servers
    """
    _ = args
    _ = cwd
    _ = timeout

    servers = _load_mcp_config()

    server_list = []
    for name, config in servers.items():
        validation_error = _validate_server_config(name, config)
        server_list.append({
            "name": name,
            "type": config.get("type", "stdio"),
            "command": config.get("command", ""),
            "url": config.get("url", ""),
            "description": config.get("description", ""),
            "valid": validation_error is None,
            "validation_error": validation_error,
        })

    output_lines = ["Available MCP servers:"]
    for s in server_list:
        status = "✓" if s["valid"] else "✗"
        output_lines.append(f"  {status} {s['name']} ({s['type']})")
        if s.get("description"):
            output_lines.append(f"    {s['description']}")
        if s.get("command"):
            output_lines.append(f"    command: {s['command']}")
        if s.get("url"):
            output_lines.append(f"    url: {s['url']}")
        if s.get("validation_error"):
            output_lines.append(f"    ERROR: {s['validation_error']}")

    return {
        "ok": True,
        "tool": "mcp_list_servers",
        "servers": server_list,
        "count": len(server_list),
        "valid_count": sum(1 for s in server_list if s["valid"]),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines) if output_lines else "No MCP servers configured",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["mcp_list_servers"],
    }


def mcp_health_check(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Check health of an MCP server.

    Usage: mcp_health_check --server <name>
    """
    server_name = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--server", "-s") and i + 1 < len(args):
            server_name = args[i + 1]
            i += 2
            continue
        i += 1

    if not server_name:
        return error_result("mcp_health_check", "Usage: mcp_health_check --server <name>")

    servers = _load_mcp_config()
    server_config = servers.get(server_name)

    if not server_config:
        return error_result("mcp_health_check", f"Server not found: {server_name}")

    validation_error = _validate_server_config(server_name, server_config)
    if validation_error:
        return error_result("mcp_health_check", validation_error)

    start = time.time()
    server_type = server_config.get("type", "stdio")

    try:
        if server_type == "stdio":
            # For stdio servers, try to call the 'health' tool if available
            result = _call_stdio_with_retry(
                server_config, "health", {}, cwd, timeout
            )
        else:
            # For HTTP servers, just check connectivity
            session = _get_http_session(server_config)
            url = server_config.get("url", "")
            response = session.get(url, timeout=5)
            response.raise_for_status()
            result = {"status": "healthy", "http_status": response.status_code}

        duration = time.time() - start

        return {
            "ok": True,
            "tool": "mcp_health_check",
            "server": server_name,
            "healthy": True,
            "result": result,
            "duration_seconds": duration,
            "error": None,
            "exit_code": 0,
            "stdout": f"Server '{server_name}' is healthy ({duration:.2f}s)",
            "stderr": "",
            "duration": duration,
            "duration_ms": int(duration * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["mcp_health_check", server_name],
        }

    except Exception as exc:
        duration = time.time() - start
        return error_result(
            "mcp_health_check",
            f"Health check failed for '{server_name}': {exc}",
            exit_code=1
        )


@_retry_with_backoff(
    max_retries=DEFAULT_MAX_RETRIES,
    delay=DEFAULT_RETRY_DELAY,
    backoff=DEFAULT_BACKOFF_FACTOR,
    exceptions=(subprocess.SubprocessError, OSError)
)
def _call_stdio_with_retry(
    server_config: Dict[str, Any],
    tool_name: str,
    tool_args: Dict[str, Any],
    cwd: str,
    timeout: int
) -> Dict[str, Any]:
    """Call a stdio MCP server with retry logic."""
    command = server_config.get("command", "")
    if isinstance(command, str):
        command = command.split()

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args
        }
    }

    logger.debug(f"Calling stdio MCP server with command: {command}")

    proc = subprocess.run(
        command,
        input=json.dumps(request),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )

    if proc.returncode != 0:
        raise subprocess.SubprocessError(f"MCP server error: {proc.stderr}")

    response = json.loads(proc.stdout)

    if "error" in response:
        raise RuntimeError(f"MCP error: {response['error']}")

    return response.get("result", {})


@_retry_with_backoff(
    max_retries=DEFAULT_MAX_RETRIES,
    delay=DEFAULT_RETRY_DELAY,
    backoff=DEFAULT_BACKOFF_FACTOR,
    exceptions=(Exception,)
)
def _call_http_with_retry(
    server_config: Dict[str, Any],
    tool_name: str,
    tool_args: Dict[str, Any],
    timeout: int
) -> Dict[str, Any]:
    """Call an HTTP MCP server with retry logic."""
    session = _get_http_session(server_config)
    url = server_config.get("url", "")
    headers = {"Content-Type": "application/json"}
    if "headers" in server_config:
        headers.update(server_config["headers"])

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args
        }
    }

    logger.debug(f"Calling HTTP MCP server at {url}")

    response = session.post(url, json=request, headers=headers, timeout=timeout)
    response.raise_for_status()

    result = response.json()

    if "error" in result:
        raise RuntimeError(f"MCP error: {result['error']}")

    return result.get("result", {})


def mcp_call(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Call an MCP tool on a server with retry and connection pooling.

    Usage: mcp_call --server <name> --tool <tool> [--args <json>] [--retry <n>]
    """
    server_name = ""
    tool_name = ""
    tool_args = "{}"
    max_retries = DEFAULT_MAX_RETRIES

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--server", "-s") and i + 1 < len(args):
            server_name = args[i + 1]
            i += 2
            continue
        if token in ("--tool", "-t") and i + 1 < len(args):
            tool_name = args[i + 1]
            i += 2
            continue
        if token in ("--args", "-a") and i + 1 < len(args):
            tool_args = args[i + 1]
            i += 2
            continue
        if token == "--retry" and i + 1 < len(args):
            try:
                max_retries = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        i += 1

    if not server_name or not tool_name:
        return error_result(
            "mcp_call",
            "Usage: mcp_call --server <name> --tool <tool> [--args <json>] [--retry <n>]"
        )

    servers = _load_mcp_config()
    server_config = servers.get(server_name)

    if not server_config:
        return error_result("mcp_call", f"Server not found: {server_name}")

    # Validate config
    validation_error = _validate_server_config(server_name, server_config)
    if validation_error:
        return error_result("mcp_call", validation_error)

    # Parse tool arguments
    try:
        args_dict = json.loads(tool_args) if tool_args != "{}" else {}
    except json.JSONDecodeError as exc:
        return error_result("mcp_call", f"Invalid JSON in args: {exc}")

    start = time.time()
    server_type = server_config.get("type", "stdio")

    try:
        if server_type == "stdio":
            result = _call_stdio_with_retry(
                server_config, tool_name, args_dict, cwd, timeout
            )
        elif server_type == "http":
            result = _call_http_with_retry(
                server_config, tool_name, args_dict, timeout
            )
        else:
            return error_result("mcp_call", f"Unsupported server type: {server_type}")

        duration = time.time() - start

        return {
            "ok": True,
            "tool": "mcp_call",
            "server": server_name,
            "called_tool": tool_name,
            "result": result,
            "error": None,
            "exit_code": 0,
            "stdout": json.dumps(result, ensure_ascii=False, indent=2),
            "stderr": "",
            "duration": duration,
            "duration_ms": int(duration * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["mcp_call", server_name, tool_name],
        }

    except subprocess.TimeoutExpired:
        return error_result("mcp_call", f"Call timed out after {timeout}s", exit_code=1)
    except Exception as exc:
        return error_result("mcp_call", str(exc), exit_code=1)


def mcp_tools(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    List tools available on an MCP server with retry.

    Usage: mcp_tools --server <name> [--retry <n>]
    """
    server_name = ""
    max_retries = DEFAULT_MAX_RETRIES

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--server", "-s") and i + 1 < len(args):
            server_name = args[i + 1]
            i += 2
            continue
        if token == "--retry" and i + 1 < len(args):
            try:
                max_retries = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        i += 1

    if not server_name:
        return error_result("mcp_tools", "Usage: mcp_tools --server <name> [--retry <n>]")

    servers = _load_mcp_config()
    server_config = servers.get(server_name)

    if not server_config:
        return error_result("mcp_tools", f"Server not found: {server_name}")

    validation_error = _validate_server_config(server_name, server_config)
    if validation_error:
        return error_result("mcp_tools", validation_error)

    start = time.time()
    server_type = server_config.get("type", "stdio")

    try:
        if server_type == "stdio":
            command = server_config.get("command", "")
            if isinstance(command, str):
                command = command.split()

            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }

            @_retry_with_backoff(
                max_retries=max_retries,
                delay=DEFAULT_RETRY_DELAY,
                backoff=DEFAULT_BACKOFF_FACTOR,
                exceptions=(subprocess.SubprocessError, OSError, json.JSONDecodeError)
            )
            def _list_stdio_tools():
                proc = subprocess.run(
                    command,
                    input=json.dumps(request),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    cwd=cwd,
                )
                if proc.returncode != 0:
                    raise subprocess.SubprocessError(f"Server error: {proc.stderr}")
                return json.loads(proc.stdout)

            response = _list_stdio_tools()
            tools = response.get("result", {}).get("tools", [])

        elif server_type == "http":
            @_retry_with_backoff(
                max_retries=max_retries,
                delay=DEFAULT_RETRY_DELAY,
                backoff=DEFAULT_BACKOFF_FACTOR,
                exceptions=(Exception,)
            )
            def _list_http_tools():
                session = _get_http_session(server_config)
                url = server_config.get("url", "")
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {}
                }
                response = session.post(url, json=request, timeout=timeout)
                response.raise_for_status()
                return response.json()

            response = _list_http_tools()
            tools = response.get("result", {}).get("tools", [])
        else:
            return error_result("mcp_tools", f"Unsupported server type: {server_type}")

        duration = time.time() - start

        return {
            "ok": True,
            "tool": "mcp_tools",
            "server": server_name,
            "tools": tools,
            "count": len(tools),
            "error": None,
            "exit_code": 0,
            "stdout": f"Tools on {server_name}:\n" + "\n".join(f"  - {t.get('name')}: {t.get('description', '')}" for t in tools),
            "stderr": "",
            "duration": duration,
            "duration_ms": int(duration * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["mcp_tools", server_name],
        }

    except Exception as exc:
        return error_result("mcp_tools", str(exc), exit_code=1)
