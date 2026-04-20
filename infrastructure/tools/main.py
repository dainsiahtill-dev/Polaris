import argparse
import json
import os

from .utils import normalize_args
from .files import (
    repo_read_slice, repo_read_around, repo_read_head, repo_read_tail, repo_write,
    file_delete, file_copy, file_move, dir_create, repo_glob
)
from .search import (
    repo_tree, repo_rg, repo_symbols_index
)
from .treesitter import (
    treesitter_outline, treesitter_find_symbol, treesitter_replace_node,
    treesitter_insert_method, treesitter_rename_symbol
)
from .repo_map import repo_map
from .context_manager import context_manager
from .cost_router import cost_router
from .linters import (
    ruff_check, ruff_format, pytest_run, coverage_run, coverage_report,
    mypy_run, jsonschema_validate, pydantic_validate
)
from .web import web_fetch, web_search
from .repl import python_run, node_run
from .mcp_client import mcp_list_servers, mcp_call, mcp_tools, mcp_validate_config, mcp_health_check
from .memory import memory_save, memory_recall, memory_list, memory_delete
from .batch import multi_file_edit
from .shell import shell_run, bash_run
from .code_analysis import dependency_graph, complexity_analysis, security_scan
from .system import env_list, system_info, process_list
from .testing import test_generate, doc_generate, api_test
from .code_quality import find_unused_imports, sort_imports, find_dead_code, regex_validate
from .data_processing import json_format, yaml_parse, hash_compute, base64_encode, base64_decode
from .network import http_request, url_validate, file_diff, version_info, cron_parse
from .semantic_search import semantic_index, semantic_search, semantic_clear
from typing import Callable, Dict, Any, List

# Tool handler type
ToolFn = Callable[[List[str], str, int], Dict[str, Any]]

# Tool handlers registry for programmatic access
TOOL_HANDLERS: Dict[str, ToolFn] = {
    # Files
    "repo_read_slice": repo_read_slice,
    "repo_read_around": repo_read_around,
    "repo_read_head": repo_read_head,
    "repo_read_tail": repo_read_tail,
    "repo_write": repo_write,
    "file_delete": file_delete,
    "file_copy": file_copy,
    "file_move": file_move,
    "dir_create": dir_create,
    "repo_glob": repo_glob,
    # Search
    "repo_tree": repo_tree,
    "repo_rg": repo_rg,
    "repo_symbols_index": repo_symbols_index,
    # Treesitter
    "treesitter_outline": treesitter_outline,
    "treesitter_find_symbol": treesitter_find_symbol,
    "treesitter_replace_node": treesitter_replace_node,
    "treesitter_insert_method": treesitter_insert_method,
    "treesitter_rename_symbol": treesitter_rename_symbol,
    # Sniper Mode
    "repo_map": repo_map,
    "context_manager": context_manager,
    "cost_router": cost_router,
    # Linters
    "ruff_check": ruff_check,
    "ruff_format": ruff_format,
    "pytest_run": pytest_run,
    "coverage_run": coverage_run,
    "coverage_report": coverage_report,
    "mypy_run": mypy_run,
    "jsonschema_validate": jsonschema_validate,
    "pydantic_validate": pydantic_validate,
    # Web
    "web_fetch": web_fetch,
    "web_search": web_search,
    # REPL
    "python_run": python_run,
    "node_run": node_run,
    # MCP
    "mcp_list_servers": mcp_list_servers,
    "mcp_call": mcp_call,
    "mcp_tools": mcp_tools,
    "mcp_validate_config": mcp_validate_config,
    "mcp_health_check": mcp_health_check,
    # Memory
    "memory_save": memory_save,
    "memory_recall": memory_recall,
    "memory_list": memory_list,
    "memory_delete": memory_delete,
    # Batch
    "multi_file_edit": multi_file_edit,
    # Shell
    "shell_run": shell_run,
    "bash_run": bash_run,
    # Code analysis
    "dependency_graph": dependency_graph,
    "complexity_analysis": complexity_analysis,
    "security_scan": security_scan,
    # System
    "env_list": env_list,
    "system_info": system_info,
    "process_list": process_list,
    # Testing
    "test_generate": test_generate,
    "doc_generate": doc_generate,
    "api_test": api_test,
    # Code quality
    "find_unused_imports": find_unused_imports,
    "sort_imports": sort_imports,
    "find_dead_code": find_dead_code,
    "regex_validate": regex_validate,
    # Data processing
    "json_format": json_format,
    "yaml_parse": yaml_parse,
    "hash_compute": hash_compute,
    "base64_encode": base64_encode,
    "base64_decode": base64_decode,
    # Network
    "http_request": http_request,
    "url_validate": url_validate,
    "file_diff": file_diff,
    "version_info": version_info,
    "cron_parse": cron_parse,
    # Semantic search
    "semantic_index": semantic_index,
    "semantic_search": semantic_search,
    "semantic_clear": semantic_clear,
}

def main():
    parser = argparse.ArgumentParser(description="Polaris Tools")
    parser.add_argument("tool", help="Tool name to execute")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments for the tool")
    
    args = parser.parse_args()
    tool_name = args.tool
    tool_args = normalize_args(args.args)
    cwd = os.getcwd()
    
    # timeout passed from outside or default? 
    # tools.py took timeout as arg in function, but main didn't seem to parse it. 
    # Usually the caller handles timeout or we pass a default.
    timeout = 30 # Default 30s
    
    result = {"ok": False, "error": f"Unknown tool: {tool_name}"}

    # Files
    if tool_name == "repo_read_slice":
        result = repo_read_slice(tool_args, cwd, timeout)
    elif tool_name == "repo_read_around":
        result = repo_read_around(tool_args, cwd, timeout)
    elif tool_name == "repo_read_head":
        result = repo_read_head(tool_args, cwd, timeout)
    elif tool_name == "repo_read_tail":
        result = repo_read_tail(tool_args, cwd, timeout)
    elif tool_name == "repo_write":
        result = repo_write(tool_args, cwd, timeout)
        
    # Search
    elif tool_name == "repo_tree":
        result = repo_tree(tool_args, cwd, timeout)
    elif tool_name == "repo_rg":
        result = repo_rg(tool_args, cwd, timeout)
    elif tool_name == "repo_symbols_index":
        result = repo_symbols_index(tool_args, cwd, timeout)
        
    # Treesitter
    elif tool_name == "treesitter_outline":
        result = treesitter_outline(tool_args, cwd, timeout)
    elif tool_name == "treesitter_find_symbol":
        result = treesitter_find_symbol(tool_args, cwd, timeout)
    elif tool_name == "treesitter_replace_node":
        result = treesitter_replace_node(tool_args, cwd, timeout)
    elif tool_name == "treesitter_insert_method":
        result = treesitter_insert_method(tool_args, cwd, timeout)
    elif tool_name == "treesitter_rename_symbol":
        result = treesitter_rename_symbol(tool_args, cwd, timeout)

    # Sniper Mode tools
    elif tool_name == "repo_map":
        result = repo_map(tool_args, cwd, timeout)
    elif tool_name == "context_manager":
        result = context_manager(tool_args, cwd, timeout)
    elif tool_name == "cost_router":
        result = cost_router(tool_args, cwd, timeout)
        
    # Linters
    elif tool_name == "ruff_check":
        result = ruff_check(tool_args, cwd, timeout)
    elif tool_name == "ruff_format":
        result = ruff_format(tool_args, cwd, timeout)
    elif tool_name == "pytest_run":
        result = pytest_run(tool_args, cwd, timeout)
    elif tool_name == "coverage_run":
        result = coverage_run(tool_args, cwd, timeout)
    elif tool_name == "coverage_report":
        result = coverage_report(tool_args, cwd, timeout)
    elif tool_name == "mypy_run":
        result = mypy_run(tool_args, cwd, timeout)
    elif tool_name == "jsonschema_validate":
        result = jsonschema_validate(tool_args, cwd, timeout)
    elif tool_name == "pydantic_validate":
        result = pydantic_validate(tool_args, cwd, timeout)

    # Web tools
    elif tool_name == "web_fetch":
        result = web_fetch(tool_args, cwd, timeout)
    elif tool_name == "web_search":
        result = web_search(tool_args, cwd, timeout)

    # REPL tools
    elif tool_name == "python_run":
        result = python_run(tool_args, cwd, timeout)
    elif tool_name == "node_run":
        result = node_run(tool_args, cwd, timeout)

    # MCP tools
    elif tool_name == "mcp_list_servers":
        result = mcp_list_servers(tool_args, cwd, timeout)
    elif tool_name == "mcp_call":
        result = mcp_call(tool_args, cwd, timeout)
    elif tool_name == "mcp_tools":
        result = mcp_tools(tool_args, cwd, timeout)
    elif tool_name == "mcp_validate_config":
        result = mcp_validate_config(tool_args, cwd, timeout)
    elif tool_name == "mcp_health_check":
        result = mcp_health_check(tool_args, cwd, timeout)

    # Memory tools
    elif tool_name == "memory_save":
        result = memory_save(tool_args, cwd, timeout)
    elif tool_name == "memory_recall":
        result = memory_recall(tool_args, cwd, timeout)
    elif tool_name == "memory_list":
        result = memory_list(tool_args, cwd, timeout)
    elif tool_name == "memory_delete":
        result = memory_delete(tool_args, cwd, timeout)

    # Batch edit tools
    elif tool_name == "multi_file_edit":
        result = multi_file_edit(tool_args, cwd, timeout)

    # File operations
    elif tool_name == "file_delete":
        result = file_delete(tool_args, cwd, timeout)
    elif tool_name == "file_copy":
        result = file_copy(tool_args, cwd, timeout)
    elif tool_name == "file_move":
        result = file_move(tool_args, cwd, timeout)
    elif tool_name == "dir_create":
        result = dir_create(tool_args, cwd, timeout)
    elif tool_name == "repo_glob":
        result = repo_glob(tool_args, cwd, timeout)

    # Shell tools
    elif tool_name == "shell_run":
        result = shell_run(tool_args, cwd, timeout)
    elif tool_name == "bash_run":
        result = bash_run(tool_args, cwd, timeout)

    # Code analysis
    elif tool_name == "dependency_graph":
        result = dependency_graph(tool_args, cwd, timeout)
    elif tool_name == "complexity_analysis":
        result = complexity_analysis(tool_args, cwd, timeout)
    elif tool_name == "security_scan":
        result = security_scan(tool_args, cwd, timeout)

    # System tools
    elif tool_name == "env_list":
        result = env_list(tool_args, cwd, timeout)
    elif tool_name == "system_info":
        result = system_info(tool_args, cwd, timeout)
    elif tool_name == "process_list":
        result = process_list(tool_args, cwd, timeout)

    # Testing & Documentation
    elif tool_name == "test_generate":
        result = test_generate(tool_args, cwd, timeout)
    elif tool_name == "doc_generate":
        result = doc_generate(tool_args, cwd, timeout)
    elif tool_name == "api_test":
        result = api_test(tool_args, cwd, timeout)

    # Code Quality
    elif tool_name == "find_unused_imports":
        result = find_unused_imports(tool_args, cwd, timeout)
    elif tool_name == "sort_imports":
        result = sort_imports(tool_args, cwd, timeout)
    elif tool_name == "find_dead_code":
        result = find_dead_code(tool_args, cwd, timeout)
    elif tool_name == "regex_validate":
        result = regex_validate(tool_args, cwd, timeout)

    # Data Processing
    elif tool_name == "json_format":
        result = json_format(tool_args, cwd, timeout)
    elif tool_name == "yaml_parse":
        result = yaml_parse(tool_args, cwd, timeout)
    elif tool_name == "hash_compute":
        result = hash_compute(tool_args, cwd, timeout)
    elif tool_name == "base64_encode":
        result = base64_encode(tool_args, cwd, timeout)
    elif tool_name == "base64_decode":
        result = base64_decode(tool_args, cwd, timeout)

    # Network & Development
    elif tool_name == "http_request":
        result = http_request(tool_args, cwd, timeout)
    elif tool_name == "url_validate":
        result = url_validate(tool_args, cwd, timeout)
    elif tool_name == "file_diff":
        result = file_diff(tool_args, cwd, timeout)
    elif tool_name == "version_info":
        result = version_info(tool_args, cwd, timeout)
    elif tool_name == "cron_parse":
        result = cron_parse(tool_args, cwd, timeout)

    # Semantic Search
    elif tool_name == "semantic_index":
        result = semantic_index(tool_args, cwd, timeout)
    elif tool_name == "semantic_search":
        result = semantic_search(tool_args, cwd, timeout)
    elif tool_name == "semantic_clear":
        result = semantic_clear(tool_args, cwd, timeout)

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
