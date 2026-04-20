import os
import time
import json
import re
from typing import List, Dict, Any
from .utils import (
    find_repo_root, ensure_within_root, error_result, Result, read_text_file
)

def _read_text_file(path: str) -> str:
    return read_text_file(path)

def _get_ts_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except ImportError:
        try:
            from tree_sitter_languages import get_parser  # type: ignore
        except Exception as exc:
             raise RuntimeError(f"tree_sitter_languages import failed: {exc}")
    except Exception as exc:
        raise RuntimeError(f"tree_sitter_languages import failed: {exc}")
    return get_parser(language)

def _ts_node_text(content: bytes, node) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

def _ts_name_node(node):
    name = node.child_by_field_name("name")
    if name is not None:
        return name
    prop = node.child_by_field_name("property")
    if prop is not None:
        return prop
    for child in node.children:
        if child.type == "identifier":
            return child
    return None

def _ts_extract_name(content: bytes, node) -> str:
    name_node = _ts_name_node(node)
    if name_node is None:
        return ""
    return _ts_node_text(content, name_node)

def _ts_iter_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            stack.append(child)

def _ts_find_symbol_nodes(content: bytes, root, symbol: str, kind: str) -> List[Dict[str, Any]]:
    symbol = symbol or ""
    wanted = {kind} if kind else {"function", "class", "method"}
    nodes: List[Dict[str, Any]] = []
    for node in _ts_iter_nodes(root):
        node_type = node.type
        is_class = node_type in {"class_definition", "class_declaration"}
        is_function = node_type in {"function_definition", "function_declaration"}
        is_method = node_type == "method_definition"
        if is_class and "class" not in wanted:
            continue
        if is_function and "function" not in wanted:
            continue
        if is_method and "method" not in wanted:
            continue
        if not (is_class or is_function or is_method):
            continue
        name = _ts_extract_name(content, node)
        if symbol and name != symbol:
            continue
        nodes.append({
            "name": name,
            "node_type": node_type,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "start_byte": node.start_byte,
            "end_byte": node.end_byte,
        })
    return nodes

def _ts_apply_replacement(file_path: str, start_byte: int, end_byte: int, replacement: str) -> None:
    with open(file_path, "rb") as handle:
        content = handle.read()
    if start_byte < 0 or end_byte < start_byte or end_byte > len(content):
        raise ValueError("Invalid byte range for replacement.")
    new_bytes = content[:start_byte] + replacement.encode("utf-8") + content[end_byte:]
    tmp_path = f"{file_path}.tmp"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(new_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, file_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def _indent_lines(text: str, indent: str) -> str:
    lines = text.splitlines()
    if not lines:
        return indent
    indented = [indent + line if line.strip() else line for line in lines]
    return "\\n".join(indented)


def treesitter_outline(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 2:
        return error_result("treesitter_outline", "Usage: treesitter_outline <language> <file>")
    language = args[0]
    root = find_repo_root(cwd)
    file_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    try:
        file_path = ensure_within_root(root, file_path)
    except ValueError as exc:
        return error_result("treesitter_outline", str(exc), exit_code=2)
    start = time.time()
    try:
        parser = _get_ts_parser(language)
    except Exception as exc:
        return error_result("treesitter_outline", str(exc), exit_code=3)
    try:
        with open(file_path, "rb") as handle:
            content = handle.read()
        tree = parser.parse(content)
        root_node = tree.root_node
        lines = []
        for child in root_node.children:
            start_row, start_col = child.start_point
            end_row, end_col = child.end_point
            name = _ts_extract_name(content, child)
            label = f"{child.type}"
            if name:
                label += f" {name}"
            lines.append(f"{label} {start_row + 1}:{start_col + 1}-{end_row + 1}:{end_col + 1}")
        output = "\n".join(lines) if lines else "(no top-level nodes)"
        return {
            "ok": True,
            "tool": "treesitter_outline",
            "file": file_path,
            "language": language,
            "entries": lines,
            "exit_code": 0,
            "stdout": output,
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["treesitter_outline"] + args,
        }
    except Exception as exc:
        return error_result("treesitter_outline", str(exc), exit_code=1)


def treesitter_find_symbol(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 3:
        return error_result("treesitter_find_symbol", "Usage: treesitter_find_symbol <language> <file> <symbol> [--kind function|class|method] [--max N]")
    language = args[0]
    root = find_repo_root(cwd)
    file_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    try:
        file_path = ensure_within_root(root, file_path)
    except ValueError as exc:
        return error_result("treesitter_find_symbol", str(exc), exit_code=2)
    symbol = args[2]
    kind = ""
    max_results = 20
    i = 3
    while i < len(args):
        token = args[i]
        if token in ("--kind", "-k") and i + 1 < len(args):
            kind = str(args[i + 1])
            i += 2
            continue
        if token in ("--max", "-m") and i + 1 < len(args):
            try:
                max_results = int(args[i + 1])
            except Exception:
                max_results = 20
            i += 2
            continue
        i += 1
    start = time.time()
    try:
        parser = _get_ts_parser(language)
    except Exception as exc:
        return error_result("treesitter_find_symbol", str(exc), exit_code=3)
    try:
        with open(file_path, "rb") as handle:
            content = handle.read()
        tree = parser.parse(content)
        matches = _ts_find_symbol_nodes(content, tree.root_node, symbol, kind)
        truncated = False
        if len(matches) > max_results:
            matches = matches[:max_results]
            truncated = True
        return {
            "ok": True,
            "tool": "treesitter_find_symbol",
            "file": file_path,
            "language": language,
            "symbol": symbol,
            "kind": kind,
            "matches": matches,
            "truncated": truncated,
            "error": None,
            "exit_code": 0,
            "stdout": json.dumps(matches, ensure_ascii=False, indent=2),
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "artifacts": [],
            "command": ["treesitter_find_symbol"] + args,
        }
    except Exception as exc:
        return error_result("treesitter_find_symbol", str(exc), exit_code=1)


def treesitter_replace_node(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 3:
        return error_result("treesitter_replace_node", "Usage: treesitter_replace_node <language> <file> <symbol> [--kind function|class|method] [--index N] [--text <code>] [--text-file path]")
    language = args[0]
    root = find_repo_root(cwd)
    file_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    try:
        file_path = ensure_within_root(root, file_path)
    except ValueError as exc:
        return error_result("treesitter_replace_node", str(exc), exit_code=2)
    symbol = args[2]
    kind = ""
    index = 0
    text_value = ""
    text_file = ""
    i = 3
    while i < len(args):
        token = args[i]
        if token in ("--kind", "-k") and i + 1 < len(args):
            kind = str(args[i + 1])
            i += 2
            continue
        if token in ("--index", "-i") and i + 1 < len(args):
            index = int(args[i + 1])
            i += 2
            continue
        if token in ("--text", "-t") and i + 1 < len(args):
            text_value = args[i + 1]
            i += 2
            continue
        if token in ("--text-file", "-f") and i + 1 < len(args):
            text_file = args[i + 1]
            i += 2
            continue
        i += 1
    if not text_value and text_file:
        text_path = os.path.join(cwd, text_file) if not os.path.isabs(text_file) else text_file
        try:
            text_path = ensure_within_root(root, text_path)
        except ValueError as exc:
            return error_result("treesitter_replace_node", str(exc), exit_code=2)
        text_value = _read_text_file(text_path)
    if text_value is None:
        text_value = ""
    
    start = time.time()
    try:
        parser = _get_ts_parser(language)
    except Exception as exc:
        return error_result("treesitter_replace_node", str(exc), exit_code=3)
    try:
        with open(file_path, "rb") as handle:
            content = handle.read()
        tree = parser.parse(content)
        matches = _ts_find_symbol_nodes(content, tree.root_node, symbol, kind)
        if not matches or index >= len(matches):
            return error_result("treesitter_replace_node", "symbol not found", exit_code=4)
        target = matches[index]
        _ts_apply_replacement(file_path, target["start_byte"], target["end_byte"], text_value)
        return {
            "ok": True,
            "tool": "treesitter_replace_node",
            "file": file_path,
            "language": language,
            "symbol": symbol,
            "kind": kind,
            "index": index,
            "start_line": target["start_line"],
            "end_line": target["end_line"],
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["treesitter_replace_node"] + args,
        }
    except Exception as exc:
        return error_result("treesitter_replace_node", str(exc), exit_code=1)


def treesitter_insert_method(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 4:
        return error_result("treesitter_insert_method", "Usage: treesitter_insert_method <language> <file> <class_name> <method_text> [--text-file path]")
    language = args[0]
    root = find_repo_root(cwd)
    file_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    try:
        file_path = ensure_within_root(root, file_path)
    except ValueError as exc:
        return error_result("treesitter_insert_method", str(exc), exit_code=2)
    class_name = args[2]
    method_text = args[3]
    text_file = ""
    i = 4
    while i < len(args):
        token = args[i]
        if token in ("--text-file", "-f") and i + 1 < len(args):
            text_file = args[i + 1]
            i += 2
            continue
        i += 1
    if text_file:
        text_path = os.path.join(cwd, text_file) if not os.path.isabs(text_file) else text_file
        try:
            text_path = ensure_within_root(root, text_path)
        except ValueError as exc:
            return error_result("treesitter_insert_method", str(exc), exit_code=2)
        method_text = _read_text_file(text_path)
    
    start = time.time()
    try:
        parser = _get_ts_parser(language)
    except Exception as exc:
        return error_result("treesitter_insert_method", str(exc), exit_code=3)
    try:
        with open(file_path, "rb") as handle:
            content = handle.read()
        tree = parser.parse(content)
        matches = _ts_find_symbol_nodes(content, tree.root_node, class_name, "class")
        if not matches:
            return error_result("treesitter_insert_method", "class not found", exit_code=4)
        target = matches[0]
        class_node = None
        for node in _ts_iter_nodes(tree.root_node):
            if node.start_byte == target["start_byte"] and node.end_byte == target["end_byte"]:
                class_node = node
                break
        if class_node is None:
            return error_result("treesitter_insert_method", "class node not resolved", exit_code=4)
        if language in ("python", "py"):
            body = class_node.child_by_field_name("body")
            if body is None:
                return error_result("treesitter_insert_method", "class body not found", exit_code=4)
            insert_byte = body.end_byte
            class_line = content[:class_node.start_byte].splitlines()[-1]
            class_indent = re.match(rb"\\s*", class_line).group(0).decode("utf-8", errors="ignore")
            method_indent = class_indent + "    "
            block = "\\n" + _indent_lines(method_text, method_indent).rstrip() + "\\n"
            _ts_apply_replacement(file_path, insert_byte, insert_byte, block)
        else:
            body = class_node.child_by_field_name("body")
            if body is None:
                return error_result("treesitter_insert_method", "class body not found", exit_code=4)
            insert_byte = body.end_byte - 1
            class_line = content[:class_node.start_byte].splitlines()[-1]
            class_indent = re.match(rb"\\s*", class_line).group(0).decode("utf-8", errors="ignore")
            method_indent = class_indent + "  "
            block = "\\n" + _indent_lines(method_text, method_indent).rstrip() + "\\n" + class_indent
            _ts_apply_replacement(file_path, insert_byte, insert_byte, block)
        return {
            "ok": True,
            "tool": "treesitter_insert_method",
            "file": file_path,
            "language": language,
            "class": class_name,
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["treesitter_insert_method"] + args,
        }
    except Exception as exc:
        return error_result("treesitter_insert_method", str(exc), exit_code=1)


def treesitter_rename_symbol(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 4:
        return error_result("treesitter_rename_symbol", "Usage: treesitter_rename_symbol <language> <file> <symbol> <new_name> [--kind function|class|method]")
    language = args[0]
    root = find_repo_root(cwd)
    file_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    try:
        file_path = ensure_within_root(root, file_path)
    except ValueError as exc:
        return error_result("treesitter_rename_symbol", str(exc), exit_code=2)
    symbol = args[2]
    new_name = args[3]
    kind = ""
    i = 4
    while i < len(args):
        token = args[i]
        if token in ("--kind", "-k") and i + 1 < len(args):
            kind = str(args[i + 1])
            i += 2
            continue
        i += 1
    
    start = time.time()
    try:
        parser = _get_ts_parser(language)
    except Exception as exc:
        return error_result("treesitter_rename_symbol", str(exc), exit_code=3)
    try:
        with open(file_path, "rb") as handle:
            content = handle.read()
        tree = parser.parse(content)
        matches = _ts_find_symbol_nodes(content, tree.root_node, symbol, kind)
        if not matches:
            return error_result("treesitter_rename_symbol", "symbol not found", exit_code=4)
        target = matches[0]
        target_node = None
        for node in _ts_iter_nodes(tree.root_node):
            if node.start_byte == target["start_byte"] and node.end_byte == target["end_byte"]:
                target_node = node
                break
        if target_node is None:
            return error_result("treesitter_rename_symbol", "symbol node not resolved", exit_code=4)
        name_node = _ts_name_node(target_node)
        if name_node is None:
            return error_result("treesitter_rename_symbol", "name node not found", exit_code=4)
        _ts_apply_replacement(file_path, name_node.start_byte, name_node.end_byte, new_name)
        return {
            "ok": True,
            "tool": "treesitter_rename_symbol",
            "file": file_path,
            "language": language,
            "symbol": symbol,
            "new_name": new_name,
            "kind": kind,
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
            "artifacts": [],
            "command": ["treesitter_rename_symbol"] + args,
        }
    except Exception as exc:
        return error_result("treesitter_rename_symbol", str(exc), exit_code=1)
