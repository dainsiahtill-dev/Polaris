import os
import time
import json
import importlib
from typing import List
from .utils import Result
from .command import run_command

# Wrapper functions for linters

def ruff_check(args: List[str], cwd: str, timeout: int) -> Result:
    return run_command(["ruff", "check"] + args, cwd, timeout)

def ruff_format(args: List[str], cwd: str, timeout: int) -> Result:
    return run_command(["ruff", "format"] + args, cwd, timeout)

def pytest_run(args: List[str], cwd: str, timeout: int) -> Result:
    return run_command(["pytest"] + args, cwd, timeout)

def coverage_run(args: List[str], cwd: str, timeout: int) -> Result:
    cmd = ["coverage", "run", "-m", "pytest"]
    if args:
        cmd = ["coverage"] + args
    return run_command(cmd, cwd, timeout)

def coverage_report(args: List[str], cwd: str, timeout: int) -> Result:
    return run_command(["coverage", "report", "-m"] + args, cwd, timeout)

def mypy_run(args: List[str], cwd: str, timeout: int) -> Result:
    return run_command(["mypy"] + args, cwd, timeout)

def jsonschema_validate(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 2:
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": "Usage: jsonschema_validate <schema.json> <data.json>",
            "duration": 0.0,
            "command": ["jsonschema_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    schema_path = os.path.join(cwd, args[0]) if not os.path.isabs(args[0]) else args[0]
    data_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    start = time.time()
    try:
        import jsonschema  # type: ignore
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 3,
            "stdout": "",
            "stderr": f"jsonschema import failed: {exc}",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["jsonschema_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    try:
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        with open(data_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        jsonschema.validate(instance=data, schema=schema)
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["jsonschema_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["jsonschema_validate"] + args,
            "truncated": False,
            "artifacts": []
        }


def pydantic_validate(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if len(args) < 2:
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": "Usage: pydantic_validate <module:ModelClass> <data.json>",
            "duration": 0.0,
            "command": ["pydantic_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    model_ref = args[0]
    data_path = os.path.join(cwd, args[1]) if not os.path.isabs(args[1]) else args[1]
    start = time.time()
    try:
        module_name, class_name = model_ref.split(":", 1)
        module = importlib.import_module(module_name)
        model = getattr(module, class_name)
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 3,
            "stdout": "",
            "stderr": f"Model import failed: {exc}",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["pydantic_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    try:
        with open(data_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if hasattr(model, "model_validate"):
            model.model_validate(data)
        elif hasattr(model, "parse_obj"):
            model.parse_obj(data)
        else:
            raise RuntimeError("Model has no pydantic validation method.")
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["pydantic_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "command": ["pydantic_validate"] + args,
            "truncated": False,
            "artifacts": []
        }
