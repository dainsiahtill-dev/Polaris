"""Compatibility facade tests for policy submodules.

These tests cover legacy imports from:
`polaris.cells.roles.kernel.internal.policy.*`
and ensure they provide deterministic behavior without placeholder stubs.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from polaris.cells.roles.kernel.internal.policy.approval_policy import ApprovalPolicy
from polaris.cells.roles.kernel.internal.policy.redaction_policy import RedactionPolicy
from polaris.cells.roles.kernel.internal.policy.sandbox_policy import SandboxPolicy
from polaris.cells.roles.kernel.internal.policy.tool_policy import ToolPolicy, ToolPolicyDecision


def test_tool_policy_evaluate_and_filter_support_legacy_shapes() -> None:
    policy = ToolPolicy(
        whitelist=["read_file"],
        allow_command_execution=False,
    )

    allowed = policy.evaluate("read_file", {"path": "README.md"})
    blocked = policy.evaluate("execute_command", {"command": "echo hi"})
    assert isinstance(allowed, ToolPolicyDecision)
    assert isinstance(blocked, ToolPolicyDecision)
    assert allowed.allowed is True
    assert blocked.allowed is False

    calls = [
        {"tool": "read_file", "args": {"path": "README.md"}},
        {"tool": "execute_command", "args": {"command": "echo hi"}},
        SimpleNamespace(tool="read_file", args={"path": "requirements.txt"}),
    ]
    filtered = policy.filter(calls)
    assert len(filtered) == 2


def test_policy_facades_support_batch_contracts() -> None:
    tool_policy = ToolPolicy.from_env()
    result = tool_policy.evaluate(
        [{"tool": "read_file", "args": {"path": "README.md"}}],
        state=SimpleNamespace(loaded_tools=["read_file"]),
    )
    # evaluate returns either ToolPolicyDecision or tuple
    assert isinstance(result, tuple)
    approved, blocked, violations = result
    assert len(approved) == 1
    assert len(blocked) == 0
    assert len(violations) == 0

    approval_policy = ApprovalPolicy.from_env()
    approval_result = approval_policy.evaluate(
        [{"tool": "execute_command", "args": {"command": "echo hi"}, "call_id": "c1"}]
    )
    assert isinstance(approval_result, tuple)
    auto_approved, requires_approval, approval_violations = approval_result
    assert len(auto_approved) == 0
    assert len(requires_approval) == 1
    assert len(approval_violations) == 0
    assert approval_policy.approve("c1") is True

    sandbox_policy = SandboxPolicy.from_env()
    sb_approved, sb_blocked, sb_violations = sandbox_policy.evaluate(
        [{"tool": "execute_command", "args": {"command": "rm -rf /"}}]
    )
    assert len(sb_approved) == 0
    assert len(sb_blocked) == 1
    assert len(sb_violations) == 1

    redaction_policy = RedactionPolicy.from_env()
    assert redaction_policy.redact("token=sk-abcdefghijklmnopqrstuvwxyz123456")


def test_approval_policy_detects_high_risk_and_command_patterns() -> None:
    policy = ApprovalPolicy()

    assert policy.requires_approval("execute_command", {"command": "echo hi"}) is True
    assert policy.requires_approval("read_file", {"path": "README.md"}) is False
    assert policy.requires_approval("read_file", {"command": "rm -rf /"}) is True

    requirement = policy.request_approval("execute_command", {"command": "echo hi"})
    assert requirement.tool_name == "execute_command"
    assert requirement.reason
    assert requirement.requested_at


def test_sandbox_policy_blocks_out_of_scope_operations() -> None:
    root = Path(".tmp_policy_compat") / uuid4().hex
    workspace = root / "workspace"
    outside = root / "outside"
    workspace.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)

    policy = SandboxPolicy()

    try:
        inside_ok = policy.evaluate_fs_scope("notes.txt", workspace=str(workspace))
        outside_blocked = policy.evaluate_fs_scope(
            str((outside / "x.txt").resolve()),
            workspace=str(workspace),
        )
        traversal_blocked = policy.evaluate_fs_scope("../secret.txt", workspace=str(workspace))
        assert inside_ok.allowed is True
        assert outside_blocked.allowed is False
        assert traversal_blocked.allowed is False

        localhost_ok = policy.evaluate_network_scope("localhost", 8080)
        blocked_port = policy.evaluate_network_scope("localhost", 22)
        external_blocked = policy.evaluate_network_scope("example.com", 443)
        assert localhost_ok.allowed is True
        assert blocked_port.allowed is False
        assert external_blocked.allowed is False

        safe_process = policy.evaluate_process_scope("python", ["-V"])
        dangerous_process = policy.evaluate_process_scope("bash", ["-c", "rm -rf /"])
        assert safe_process.allowed is True
        assert dangerous_process.allowed is False

        safe_env = policy.evaluate_env_scope({"PATH": "x"})
        forbidden_env = policy.evaluate_env_scope({"API_KEY": "secret"})
        assert safe_env.allowed is True
        assert forbidden_env.allowed is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_redaction_policy_redacts_sensitive_content_recursively() -> None:
    policy = RedactionPolicy()
    token = "sk-abcdefghijklmnopqrstuvwxyz123456"
    email = "alice@example.com"

    redacted_text = policy.redact_log(f"token={token} email={email}")
    assert token not in redacted_text
    assert email not in redacted_text

    payload = {
        "token": token,
        "nested": {"email": email},
        "credential_line": "password=abc123",
    }
    redacted_payload = policy.redact_dict(payload)
    assert token not in str(redacted_payload)
    assert email not in str(redacted_payload)
    assert "abc123" not in str(redacted_payload)
