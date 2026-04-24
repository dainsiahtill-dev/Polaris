"""Token estimation and budget management service.

Provides token counting, budget tracking, and cost control.

This module is the canonical location for TokenService.
Infrastructure provides platform-specific implementations via DI.

Persistence contract:
  All state is flushed through KernelOne FS (``KernelFileSystem``) so that the
  same UTF-8 + audit-trail guarantees apply.  The ``state_file`` parameter
  accepts a ``pathlib.Path`` for backward-compat path resolution but all I/O
  is delegated to KFS — ``Path.write_text`` is never called directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from polaris.kernelone.fs import KernelFileSystem
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter

logger = logging.getLogger(__name__)


@dataclass
class TokenEstimate:
    """Token estimate result."""

    prompt_tokens: int
    completion_tokens: int = 0
    total_tokens: int = field(init=False)
    estimated: bool = True  # True if using heuristic, False if from API

    def __post_init__(self) -> None:
        self.total_tokens = self.prompt_tokens + self.completion_tokens


@dataclass
class BudgetStatus:
    """Current budget status."""

    used_tokens: int
    budget_limit: int | None
    remaining_tokens: int | None = field(init=False)
    percent_used: float = field(init=False)
    is_exceeded: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.budget_limit is not None and self.budget_limit > 0:
            self.remaining_tokens = max(0, self.budget_limit - self.used_tokens)
            self.percent_used = (self.used_tokens / self.budget_limit) * 100
            self.is_exceeded = self.used_tokens >= self.budget_limit
        else:
            self.remaining_tokens = None
            self.percent_used = 0.0
            self.is_exceeded = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "used_tokens": self.used_tokens,
            "budget_limit": self.budget_limit,
            "remaining_tokens": self.remaining_tokens,
            "percent_used": self.percent_used,
            "is_exceeded": self.is_exceeded,
        }


def _get_default_adapter() -> KernelFileSystemAdapter:
    """Lazily resolve the default KFS adapter at call time, not import time."""
    from polaris.kernelone.fs.registry import get_default_adapter

    return get_default_adapter()


class TokenService:
    """Service for token estimation and budget management.

    Provides:
    - Token estimation (delegates to canonical TokenEstimator)
    - Budget tracking and limits
    - Output truncation decisions
    - Cost estimation

    Note:
        Token estimation delegates to ``polaris.kernelone.llm.engine.TokenEstimator``
        for the actual estimation logic. This service provides domain-specific
        features like budget tracking and state persistence.
    """

    # Delegate to canonical TokenEstimator for estimation
    # Keep CHARS_PER_TOKEN for backward compatibility with code that may reference it
    CHARS_PER_TOKEN = 4

    # Output limits
    MAX_OUTPUT_SIZE = 50 * 1024  # 50KB
    MAX_OUTPUT_LINES = 1000
    PREVIEW_SIZE = 1000  # Characters for preview

    def __init__(
        self,
        budget_limit: int | None = None,
        state_file: Path | None = None,
        *,
        fs: KernelFileSystem | None = None,
        kfs_logical_path: str | None = None,
    ) -> None:
        """Initialize token service.

        Args:
            budget_limit: Maximum token budget (None for unlimited).
            state_file: Legacy parameter kept for backward compatibility.  When
                        ``kfs_logical_path`` is not provided the logical path is
                        derived from ``state_file.name`` under
                        ``runtime/state/budget/``.  When both are ``None`` state
                        is kept in-memory only (no persistence).
            fs: Optional injected ``KernelFileSystem``.  When ``None`` the
                default adapter from the registry is used if persistence is
                configured.
            kfs_logical_path: Explicit KFS logical path for the state file,
                e.g. ``"runtime/state/budget/token_svc.json"``.  Preferred over
                ``state_file`` for new code.
        """
        self.budget_limit = budget_limit
        self.state_file = state_file
        self._used_tokens = 0
        self._fs: KernelFileSystem | None = None

        # Resolve the KFS logical path (preferred) or derive from state_file name.
        if kfs_logical_path is not None:
            self._kfs_logical_path: str | None = str(kfs_logical_path).strip() or None
        elif state_file is not None:
            self._kfs_logical_path = f"runtime/state/budget/{state_file.name}"
        else:
            self._kfs_logical_path = None

        if self._kfs_logical_path is not None:
            if fs is not None:
                self._fs = fs
            else:
                try:
                    from polaris.kernelone.fs import KernelFileSystem

                    adapter = _get_default_adapter()
                    self._fs = KernelFileSystem(".", adapter)
                except RuntimeError:
                    # Default adapter not bootstrapped — persistence disabled.
                    logger.debug(
                        "token_service: KFS adapter not available; state will not be persisted to %s",
                        self._kfs_logical_path,
                    )

        # Load persisted state if available
        if self._kfs_logical_path and self._fs is not None:
            self._load_state()

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Delegates to the canonical TokenEstimator for accurate estimation.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

        return TokenEstimator.estimate(text)

    def estimate_message_tokens(
        self,
        content: str,
        role: str = "user",
    ) -> TokenEstimate:
        """Estimate tokens for a message.

        Args:
            content: Message content
            role: Message role (system, user, assistant)

        Returns:
            TokenEstimate
        """
        # Base tokens for message structure
        base_tokens = 4  # Formatting overhead

        # Role overhead
        role_overhead = {
            "system": 2,
            "user": 3,
            "assistant": 3,
        }.get(role, 3)

        content_tokens = self.estimate_tokens(content)

        return TokenEstimate(
            prompt_tokens=base_tokens + role_overhead + content_tokens,
            estimated=True,
        )

    def should_truncate_output(
        self,
        output: str,
        max_size: int | None = None,
    ) -> tuple[bool, int]:
        """Determine if output should be truncated.

        Args:
            output: Output text
            max_size: Maximum size (uses MAX_OUTPUT_SIZE if None)

        Returns:
            Tuple of (should_truncate, original_line_count)
        """
        max_size = max_size or self.MAX_OUTPUT_SIZE

        # Check size
        if len(output) <= max_size:
            return False, output.count("\n")

        return True, output.count("\n")

    def truncate_output(
        self,
        output: str,
        max_size: int | None = None,
        add_notice: bool = True,
    ) -> str:
        """Truncate output to maximum size.

        Args:
            output: Output text
            max_size: Maximum size (uses MAX_OUTPUT_SIZE if None)
            add_notice: Whether to add truncation notice

        Returns:
            Truncated output
        """
        should_truncate, original_lines = self.should_truncate_output(output, max_size)

        if not should_truncate:
            return output

        max_size = max_size or self.MAX_OUTPUT_SIZE

        # Reserve space for the notice so final output always fits within max_size
        reserved_notice_space = 100
        safe_limit = max_size - reserved_notice_space

        # Find last newline within the safe limit
        prefix = output[:safe_limit]
        last_newline = prefix.rfind("\n")

        truncated = prefix[:last_newline] if last_newline >= 0 else prefix

        if add_notice:
            truncated_lines = truncated.count("\n")
            truncated += (
                f"\n\n[...{original_lines - truncated_lines} lines truncated ({len(output) - len(truncated)} bytes)...]"
            )

        return truncated

    def create_preview(self, output: str, preview_size: int | None = None) -> str:
        """Create a preview of output.

        Args:
            output: Full output
            preview_size: Preview size in characters

        Returns:
            Preview text
        """
        preview_size = preview_size or self.PREVIEW_SIZE

        if len(output) <= preview_size:
            return output

        # Reserve space for the notice so final output always fits within preview_size + small buffer
        reserved_notice_space = 30
        safe_preview_size = preview_size - reserved_notice_space
        preview = output[:safe_preview_size]

        # Cut at last newline if possible
        last_newline = preview.rfind("\n")
        if last_newline > safe_preview_size * 0.8:
            preview = preview[:last_newline]

        remaining = len(output) - len(preview)
        return preview + f"\n[...{remaining} more characters...]"

    def record_usage(self, tokens: int) -> None:
        """Record token usage.

        Args:
            tokens: Number of tokens used
        """
        self._used_tokens += tokens
        self._persist_state()

    def get_budget_status(self) -> BudgetStatus:
        """Get current budget status.

        Returns:
            BudgetStatus
        """
        return BudgetStatus(
            used_tokens=self._used_tokens,
            budget_limit=self.budget_limit,
        )

    def check_budget(self, estimated_tokens: int) -> tuple[bool, str]:
        """Check if estimated tokens fit within budget.

        Args:
            estimated_tokens: Estimated tokens to use

        Returns:
            Tuple of (is_allowed, reason)
        """
        if self.budget_limit is None:
            return True, "No budget limit"

        projected = self._used_tokens + estimated_tokens

        if projected > self.budget_limit:
            return (
                False,
                f"Budget exceeded: {projected}/{self.budget_limit} tokens",
            )

        if projected > self.budget_limit * 0.9:
            return (
                True,
                f"WARNING: Approaching budget limit ({projected}/{self.budget_limit})",
            )

        return True, f"OK: {projected}/{self.budget_limit} tokens"

    def _persist_state(self) -> None:
        """Persist token usage state via KernelOne FS.

        All I/O goes through ``KernelFileSystem.write_json`` — never through
        ``Path.write_text`` directly.  If KFS is unavailable (adapter not
        bootstrapped) the operation is a no-op so the caller is not blocked.
        """
        if self._kfs_logical_path is None or self._fs is None:
            return
        try:
            state: dict[str, Any] = {
                "used_tokens": self._used_tokens,
                "budget_limit": self.budget_limit,
            }
            self._fs.write_json(
                self._kfs_logical_path,
                state,
                indent=2,
                ensure_ascii=False,
            )
        except (RuntimeError, ValueError) as exc:
            logger.debug("token_service: failed to persist state: %s", exc)

    def _load_state(self) -> None:
        """Load persisted state via KernelOne FS."""
        if self._kfs_logical_path is None or self._fs is None:
            return
        try:
            if not self._fs.exists(self._kfs_logical_path):
                return
            raw = self._fs.read_text(self._kfs_logical_path, encoding="utf-8")
            state = json.loads(raw)
            self._used_tokens = state.get("used_tokens", 0)
        except (RuntimeError, ValueError) as exc:
            logger.debug("token_service: failed to load state: %s", exc)


# Global instance
_token_service: TokenService | None = None


def get_token_service(
    budget_limit: int | None = None,
    state_file: Path | None = None,
) -> TokenService:
    """Get or create global token service.

    Args:
        budget_limit: Token budget limit
        state_file: State persistence file

    Returns:
        TokenService instance
    """
    global _token_service

    if _token_service is None:
        _token_service = TokenService(budget_limit, state_file)

    return _token_service


def reset_token_service() -> None:
    """Reset global token service (for testing)."""
    global _token_service
    _token_service = None


def estimate_tokens(text: str) -> int:
    """Quick token estimation (global function).

    Args:
        text: Text to estimate

    Returns:
        Estimated token count
    """
    return get_token_service().estimate_tokens(text)


__all__ = [
    "BudgetStatus",
    "TokenEstimate",
    "TokenService",
    "estimate_tokens",
    "get_token_service",
    "reset_token_service",
]
