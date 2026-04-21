"""VerificationGuard Cell - Agent Documentation

## Overview

The VerificationGuard Cell implements the **"Verification Before Completion"** pattern,
inspired by Superpowers design principles. It ensures that no completion claim is
accepted without fresh, verifiable evidence.

## Core Principle

> **"没有新鲜验证证据就不能声称完成"**
> (No fresh verification evidence, no completion claim.)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    VerificationGuard Cell                    │
├─────────────────────────────────────────────────────────────┤
│  Public Contracts (contracts.py)                             │
│  ├── VerificationClaim      - Completion claim with evidence │
│  ├── VerificationReport     - Detailed verification result   │
│  ├── VerifyCompletionCommandV1 - Entry point command         │
│  └── VerifyCompletionResultV1  - Canonical result            │
├─────────────────────────────────────────────────────────────┤
│  Internal Components                                         │
│  ├── verification_engine.py - Core verification logic        │
│  │   ├── verify()           - Main entry point               │
│   │   ├── _match_outcome()  - Fuzzy outcome matching         │
│   │   └── _collect_evidence() - Evidence collection          │
│  └── safe_executor.py       - Security-focused execution     │
│      ├── validate_command_safety() - Whitelist validation    │
│      └── execute()          - Timeout-enforced execution     │
└─────────────────────────────────────────────────────────────┘
```

## Workflow

1. **识别 (Identify)**: Parse the completion claim
2. **运行 (Run)**: Execute verification commands safely
3. **读取 (Read)**: Collect evidence from specified paths
4. **验证 (Verify)**: Match claimed outcome against actual results
5. **声明 (Declare)**: Generate PASS/FAIL/BLOCKED/TIMEOUT report

## Security Features

- **Command Whitelist**: Only pre-approved commands allowed
  - Allowed: `pytest`, `python -m`, `ruff`, `mypy`, `npm test`, etc.
  - Blocked: `rm`, `sudo`, `curl | sh`, `eval`, `exec`
- **Shell Injection Detection**: Prevents command chaining attacks
- **Path Traversal Protection**: Evidence paths restricted to workspace
- **Timeout Enforcement**: Default 60s, configurable per claim
- **Output Size Limits**: Prevents memory exhaustion from large outputs

## Usage Example

```python
from polaris.cells.factory.verification_guard.public.contracts import (
    VerificationClaim,
    VerifyCompletionCommandV1,
)
from polaris.cells.factory.verification_guard.internal.verification_engine import (
    VerificationEngine,
)

# Create a claim
claim = VerificationClaim(
    claim_id="task-123",
    claimed_outcome="tests pass",
    verification_commands=["pytest tests/ -v"],
    evidence_paths=["test_report.xml"],
    timeout_seconds=60,
)

# Verify the claim
engine = VerificationEngine()
report = engine.verify(claim, workspace="/path/to/project")

if report.status == VerificationStatus.PASS:
    print("✓ Verification passed - completion accepted")
else:
    print(f"✗ Verification failed: {report.execution_summary}")
    for detail in report.mismatch_details:
        print(f"  - {detail}")
```

## Integration Points

- **Current**: Standalone Cell with public contracts
- **Future**: `TurnTransactionController` will call this Cell before `commit()`
- **Events**: Emits `VerificationCompletedEventV1` for audit trails

## Testing

Run tests:
```bash
pytest polaris/cells/factory/verification_guard/tests/ -v
```

Test coverage includes:
- Normal scenarios (successful verification)
- Boundary scenarios (empty inputs, timeouts, whitelist violations)
- Exception scenarios (command failures, resource exhaustion)
- Regression scenarios (false completion detection)

## Cell Metadata

- **ID**: `factory.verification_guard`
- **Kind**: `capability`
- **Owner**: `factory`
- **Visibility**: `public`
- **Stateful**: `false`

## Dependencies

- `factory.pipeline` - For workflow integration
- `policy.workspace_guard` - For workspace boundary enforcement

## Verification

- **Tests**: `polaris/cells/factory/verification_guard/tests/test_verification_guard.py`
- **Smoke**: `pytest polaris/cells/factory/verification_guard/tests/ -v`
