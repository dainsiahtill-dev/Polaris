"""Synthetic input data only; not an authority source or a production API."""

from __future__ import annotations

from typing import Any


def synthetic_task_intent_portfolio_payload() -> dict[str, Any]:
    return {
        "construction_plan": {
            "task_plans": {"TASK-A": {"steps": ["Implement CLI", "Verify CLI"]}},
            "project_interface_contract": {},
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "source",
                        "path": "src/main.py",
                        "semantic_role": "entrypoint",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                    {
                        "obligation_id": "tests",
                        "path": "tests/test_main.py",
                        "semantic_role": "test",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                    {
                        "obligation_id": "manifest",
                        "path": "pyproject.toml",
                        "semantic_role": "manifest",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                ],
                "entrypoints": [
                    {
                        "obligation_id": "cli",
                        "kind": "cli",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                        "source_path": "src/main.py",
                        "runtime_path": None,
                        "command": "python -m src.main",
                    }
                ],
                "verification": [
                    {
                        "obligation_id": "environment",
                        "modality": "environment_prep",
                        "command_authority_hash": None,
                        "applicability": "required",
                        "covers_obligation_ids": ["manifest"],
                        "owner_task_id": "TASK-A",
                        "argv": ["python", "-m", "pip", "install", "-e", "."],
                        "cwd": ".",
                    },
                    {
                        "obligation_id": "check",
                        "modality": "test",
                        "command_authority_hash": None,
                        "applicability": "required",
                        "covers_obligation_ids": ["source", "tests"],
                        "owner_task_id": "TASK-A",
                        "argv": ["python", "-m", "pytest", "-q"],
                        "cwd": ".",
                    },
                ],
            }
        },
        "risk_flags": [],
    }
