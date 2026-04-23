"""Configuration constants and environment setup for loop-pm."""

import logging
import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# 统一从core层导入UTF-8工具函数，消除重复代码
# ═══════════════════════════════════════════════════════════════════
from polaris.domain.entities import DEFAULT_DEFECT_TICKET_FIELDS
from polaris.kernelone.fs.encoding import (
    build_utf8_env,
    enforce_utf8,
)

logger = logging.getLogger(__name__)

# Directories and paths
SCRIPT_DIR = str(Path(__file__).resolve().parent.parent)


def _resolve_project_root() -> str:
    """Resolve project root with environment override first."""
    from_env = str(os.environ.get("KERNELONE_PROJECT_ROOT", "") or "").strip()
    if from_env:
        return str(Path(from_env).expanduser().resolve())
    resolved = Path(__file__).resolve()
    parents = resolved.parents
    if len(parents) > 6:
        # .../src/backend/polaris/delivery/cli/pm/config.py -> repo root
        return str(parents[6])
    return str(parents[-1])


PROJECT_ROOT = _resolve_project_root()

# Environment variable names
PROMPT_PROFILE_ENV = "KERNELONE_PROMPT_PROFILE"
AGENTS_APPROVAL_MODE_ENV = "KERNELONE_AGENTS_APPROVAL_MODE"
AGENTS_APPROVAL_TIMEOUT_ENV = "KERNELONE_AGENTS_APPROVAL_TIMEOUT_SECONDS"

# Default paths
DEFAULT_DIRECTOR_SUBPROCESS_LOG = "runtime/logs/director.process.log"
DEFAULT_DIRECTOR_STATUS = "runtime/status/director.status.json"
AGENTS_DRAFT_REL = "runtime/contracts/agents.generated.md"
AGENTS_FEEDBACK_REL = "runtime/contracts/agents.feedback.md"
CANONICAL_PM_TASKS_REL = "runtime/contracts/pm_tasks.contract.json"
DEFAULT_ASSIGNEE_EXECUTION = "runtime/state/assignee_execution.state.json"

# Approval modes
AGENTS_APPROVAL_MODES = {"auto", "wait", "auto_accept", "fail_fast"}
DEFAULT_AGENTS_APPROVAL_MODE = "auto_accept"
DEFAULT_AGENTS_APPROVAL_TIMEOUT = 90
MANUAL_INTERVENTION_MODE_ENV = "KERNELONE_MANUAL_INTERVENTION_MODE"
MANUAL_INTERVENTION_MODES = {"report_only", "pause"}
DEFAULT_MANUAL_INTERVENTION_MODE = "report_only"

# Required module files for loop module discovery
REQUIRED_MODULE_FILES = (
    "decision.py",
    "codex_utils.py",
    "io_utils.py",
    "policy.py",
    "ollama_utils.py",
    "prompts.py",
    "shared.py",
)

# Task status constants
SUPPORTED_ASSIGNEES = {
    "Architect",
    "ChiefEngineer",
    "PM",
    "Director",
    "Auditor",
    "PolicyGate",
    "FinOps",
}
ACTIVE_TASK_STATUSES = {"todo", "in_progress", "review", "needs_continue"}
TERMINAL_TASK_STATUSES = {"done", "failed", "blocked"}

# Schema fields
DEFAULT_PM_SCHEMA_REQUIRED_FIELDS = [
    "id",
    "priority",
    "dependencies",
    "spec",
    "acceptance_criteria",
    "assigned_to",
]

# Status constants
MANUAL_INTERVENTION_STATUS = "MANUAL_INTERVENTION_REQUIRED"
MANUAL_INTERVENTION_RESUME_NOTE = "Resumed prior task after manual intervention."
PM_SPIN_GUARD_STATUS = "PM_SPIN_GUARD_ACTIVE"

# Priority aliases
PRIORITY_ALIASES = {
    "urgent": 0,
    "highest": 0,
    "high": 1,
    "normal": 5,
    "medium": 5,
    "low": 9,
}

# Role auto-assignment keywords
ARCHITECT_KEYWORDS = [
    "architect",
    "design",
    "spec",
    "blueprint",
    "structure",
    "schema",
    "api design",
    "interface design",
]
CHIEF_ENGINEER_KEYWORDS = [
    "chief engineer",
    "chiefengineer",
    "工部尚书",
    "module graph",
    "dependency graph",
    "import graph",
    "code blueprint",
    "blueprint index",
    "scope planning",
    "verify readiness",
    "build readiness",
    "framework map",
]
POLICY_KEYWORDS = [
    "policy",
    "compliance",
    "lint",
    "type check",
    "invariant",
    "security audit",
]
FINOPS_KEYWORDS = ["budget", "cost", "token", "finops", "spending", "usage limit"]
AUDIT_KEYWORDS = ["review", "audit", "qa", "quality", "acceptance", "verify result"]


def _is_valid_module_dir(path: str) -> bool:
    """Check if path is a valid module directory containing required files."""
    if not path:
        return False
    candidate = os.path.abspath(path)
    if not os.path.isdir(candidate):
        return False
    return all(os.path.isfile(os.path.join(candidate, filename)) for filename in REQUIRED_MODULE_FILES)


def find_module_dir(base_dir: str) -> str:
    """Find the loop module directory."""
    env_candidates = [os.environ.get("KERNELONE_LOOP_MODULE_DIR", "").strip()]
    for env_dir in env_candidates:
        if _is_valid_module_dir(env_dir):
            return os.path.abspath(env_dir)
    candidates = [
        os.path.join(base_dir, "..", "core", "polaris_loop"),
        os.path.join(base_dir, "polaris-loop"),
        os.path.join(base_dir, "..", "modules", "polaris-loop"),
        os.path.join(base_dir, "..", "modules", "ollama-loop"),
    ]
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if _is_valid_module_dir(candidate):
            return candidate
    return ""


# Module directory setup
MODULE_DIR = os.environ.get("KERNELONE_LOOP_MODULE_DIR", "").strip()
if MODULE_DIR:
    MODULE_DIR = os.path.abspath(MODULE_DIR)
if not _is_valid_module_dir(MODULE_DIR):
    MODULE_DIR = find_module_dir(SCRIPT_DIR)
if not MODULE_DIR:
    # Keep module import side-effect free: tests and tooling may import polaris.delivery.cli.pm.config
    # without runtime loop modules present.
    MODULE_DIR = ""


class PmRoleState:
    """State container for PM role operations."""

    def __init__(
        self,
        workspace_full: str,
        cache_root_full: str,
        model: str,
        show_output: bool,
        timeout: int,
        prompt_profile: str,
        output_path: str,
        events_path: str,
        log_path: str,
        llm_events_path: str = "",
    ) -> None:
        self.workspace_full = workspace_full
        self.cache_root_full = cache_root_full
        self.model = model
        self.show_output = show_output
        self.timeout = timeout
        self.prompt_profile = prompt_profile
        self.ollama_full = output_path
        self.events_full = events_path
        self.log_full = log_path
        self.llm_events_full = llm_events_path


def load_pm_model_config() -> tuple[str, str]:
    """Load PM role model configuration from visual config."""
    try:
        from polaris.kernelone.llm.runtime_config import get_role_model

        provider_id, model = get_role_model("pm")

        # Set environment variables for downstream use
        os.environ["KERNELONE_PM_PROVIDER"] = provider_id
        os.environ["KERNELONE_PM_MODEL"] = model

        return provider_id, model
    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to load visual PM config: %s", e)
        # Fallback to environment variables or defaults
        provider_id = os.environ.get("KERNELONE_PM_PROVIDER", "openai")
        model = os.environ.get("KERNELONE_PM_MODEL", "gpt-4")
        return provider_id, model


# Keep module import side-effect free:
# Do not trigger runtime config loading at import time.
_PM_PROVIDER_ID = os.environ.get("KERNELONE_PM_PROVIDER", "openai")
_PM_MODEL = os.environ.get("KERNELONE_PM_MODEL", "gpt-4")


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "AGENTS_APPROVAL_MODES",
    "AGENTS_APPROVAL_MODE_ENV",
    "AGENTS_APPROVAL_TIMEOUT_ENV",
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "ARCHITECT_KEYWORDS",
    "AUDIT_KEYWORDS",
    "CANONICAL_PM_TASKS_REL",
    "CHIEF_ENGINEER_KEYWORDS",
    "DEFAULT_AGENTS_APPROVAL_MODE",
    "DEFAULT_AGENTS_APPROVAL_TIMEOUT",
    "DEFAULT_ASSIGNEE_EXECUTION",
    "DEFAULT_DEFECT_TICKET_FIELDS",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_MANUAL_INTERVENTION_MODE",
    "DEFAULT_PM_SCHEMA_REQUIRED_FIELDS",
    "FINOPS_KEYWORDS",
    "MANUAL_INTERVENTION_MODES",
    "MANUAL_INTERVENTION_MODE_ENV",
    "MANUAL_INTERVENTION_RESUME_NOTE",
    "MANUAL_INTERVENTION_STATUS",
    "MODULE_DIR",
    "PM_SPIN_GUARD_STATUS",
    "POLICY_KEYWORDS",
    "PRIORITY_ALIASES",
    "PROJECT_ROOT",
    "PROMPT_PROFILE_ENV",
    "REQUIRED_MODULE_FILES",
    "SCRIPT_DIR",
    "SUPPORTED_ASSIGNEES",
    "TERMINAL_TASK_STATUSES",
    "_PM_MODEL",
    "_PM_PROVIDER_ID",
    "PmRoleState",
    "build_utf8_env",
    "enforce_utf8",
    "load_pm_model_config",
]
