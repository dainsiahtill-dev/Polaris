---
name: mcp-builder
description: Build Model Context Protocol (MCP) servers and tools
tags: [mcp, tools, integration]
---

# MCP Builder Skill

Use this skill when building Model Context Protocol (MCP) servers and tools.

## MCP Overview

MCP is a protocol for extending AI assistants with custom tools.
Components:
- **Tools**: Functions the AI can call
- **Resources**: Data sources the AI can read
- **Prompts**: Reusable prompt templates

## Tool Design

### Tool Schema
```json
{
  "name": "tool_name",
  "description": "What this tool does",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {
        "type": "string",
        "description": "What this param does"
      }
    },
    "required": ["param1"]
  }
}
```

### Implementation Template (Python)
```python
from mcp.server import Server
from mcp.types import TextContent

app = Server("my-server")

@app.call_tool()
async def my_tool(name: str, arguments: dict):
    if name != "tool_name":
        raise ValueError(f"Unknown tool: {name}")

    param1 = arguments["param1"]

    # Validate inputs
    if not param1:
        raise ValueError("param1 is required")

    # Execute logic
    result = await do_something(param1)

    return [TextContent(type="text", text=result)]

# Run server
async def main():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read, write):
        await app.run(read, write)
```

## Best Practices

### 1. Input Validation
- Validate all parameters
- Use type hints
- Return clear errors

### 2. Error Handling
```python
try:
    result = risky_operation()
except FileNotFoundError as e:
    return [TextContent(type="text", text=f"Error: File not found: {e}")]
except PermissionError as e:
    return [TextContent(type="text", text=f"Error: Permission denied: {e}")]
```

### 3. Output Formatting
- Use structured output when possible
- Limit output size (truncation)
- Include relevant metadata

### 4. Security
- Sandboxed paths
- No arbitrary code execution
- Validate file paths

## Testing MCP Tools

```python
import pytest
from my_mcp_server import app

@pytest.mark.asyncio
async def test_my_tool():
    result = await app.call_tool("tool_name", {"param1": "test"})

    assert len(result) == 1
    assert result[0].type == "text"
    assert "expected" in result[0].text
```

## Common Tool Patterns

### File Operations
```python
@app.call_tool()
async def read_file(path: str):
    # Validate path is within workspace
    safe_path = validate_path(path)

    content = safe_path.read_text()

    # Truncate if too large
    if len(content) > 50000:
        content = content[:50000] + "\n[...truncated]"

    return [TextContent(type="text", text=content)]
```

### Command Execution
```python
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r">\s*/dev/sda",
    # ...
]

@app.call_tool()
async def run_command(command: str):
    # Security check
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            raise ValueError(f"Dangerous command detected: {pattern}")

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        timeout=60,
    )

    return [
        TextContent(type="text", text=result.stdout),
        TextContent(type="text", text=result.stderr),
    ]
```

## Resources

Resources are read-only data sources:

```python
@app.list_resources()
async def list_resources():
    return [
        Resource(
            uri="file:///docs/readme.md",
            name="README",
            mimeType="text/markdown",
        )
    ]

@app.read_resource()
async def read_resource(uri: str):
    path = parse_uri(uri)
    return path.read_text()
```
