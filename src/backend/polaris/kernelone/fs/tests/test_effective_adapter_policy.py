"""Registered adapter policy must survive inheritance and instance overrides."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.contracts import NamespaceOperationUnavailableError

# Literal public operation, managed contract, and legacy policy it replaces.
_OPERATIONS = [
    ("read_text", "read_managed_namespace_bytes", "read_text"),
    ("write_text", "write_managed_namespace_bytes", "write_text"),
    ("write_text_atomic", "write_managed_namespace_bytes_atomic", "write_text"),
    ("append_text", "append_managed_namespace_bytes", "append_text"),
    ("remove", "remove_managed_namespace", "remove"),
    ("create", "create_managed_namespace_bytes", "write_text"),
    ("workspace_atomic", "write_managed_text_atomic", "write_text"),
]


@pytest.mark.parametrize("operation,capability,legacy", _OPERATIONS)
@pytest.mark.parametrize(
    "shape", ["child", "grandchild", "instance", "new_policy", "inherited_opt_in", "instance_opt_in"]
)
def test_effective_adapter_policy_stays_authoritative(
    tmp_path: Path, operation: str, capability: str, legacy: str, shape: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".polaris" / "runtime" / "target"
    target.parent.mkdir(parents=True)
    if operation != "create":
        target.write_text("before", encoding="utf-8")

    def deny(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("synthetic legacy policy denial")

    def opt_in(self: Any, *args: Any, **kwargs: Any) -> Any:
        return getattr(LocalFileSystemAdapter, capability)(self, *args, **kwargs)

    refusing = type("Refusing", (LocalFileSystemAdapter,), {legacy: deny})
    opted_in = type("OptedIn", (refusing,), {capability: opt_in})
    if shape == "child":
        adapter = type("Child", (refusing,), {})()
    elif shape == "grandchild":
        child = type("Child", (refusing,), {})
        adapter = type("Grandchild", (child,), {})()
    elif shape == "new_policy":
        adapter = type("ChangedPolicy", (opted_in,), {legacy: deny})()
    elif shape == "inherited_opt_in":
        adapter = type("InheritedOptIn", (opted_in,), {})()
    else:
        adapter = LocalFileSystemAdapter()
        setattr(adapter, legacy, types.MethodType(deny, adapter))
        if shape == "instance_opt_in":
            setattr(adapter, capability, types.MethodType(opt_in, adapter))
    fs = KernelFileSystem(str(workspace), adapter, read_only_paths=True, runtime_owner_workspace=str(workspace))

    def invoke() -> Any:
        if operation == "workspace_atomic":
            return fs.workspace_write_text_atomic(".polaris/runtime/target", "after", encoding="utf-8")
        if operation == "create":
            return fs.workspace_create_text(".polaris/runtime/new/leaf", "after", encoding="utf-8")
        if operation in {"read_text", "remove"}:
            return getattr(fs, operation)("runtime/target")
        return getattr(fs, operation)("runtime/target", "after", encoding="utf-8")

    permitted = shape in {"inherited_opt_in", "instance_opt_in"}
    if not permitted:
        refusal = NotImplementedError if operation == "workspace_atomic" else NamespaceOperationUnavailableError
        with pytest.raises(refusal):
            invoke()
        if operation != "create":
            assert target.read_text(encoding="utf-8") == "before"
        assert not (target.parent / "new").exists()
        return
    result = invoke()
    if operation == "read_text":
        assert result == "before"
    elif operation == "remove":
        assert result is True
        assert not target.exists()
    elif operation == "create":
        assert result.created is True
        assert (target.parent / "new" / "leaf").read_text(encoding="utf-8") == "after"
    else:
        assert target.read_text(encoding="utf-8") == ("beforeafter" if operation == "append_text" else "after")
