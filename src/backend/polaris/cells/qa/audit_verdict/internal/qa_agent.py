"""QA Agent - lightweight approval and review role agent.

This implementation follows the current RoleAgent contract in
`role_agent/agent_runtime_base.py` and provides:
- review intake and status tracking
- protocol-based approval workflow
- message-driven interactions with PM/Director
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

# Cross-Cell import: AgentMessage and MessageType come from the shared kernelone
# contracts layer to avoid loading roles.runtime.internal modules that transitively
# pull in qa.audit_verdict.internal, which would create a circular dependency.
# RoleAgent is kept from roles.runtime.public.contracts to preserve the full ABC
# interface (including initialize(), start(), stop(), etc.).
from polaris.cells.roles.runtime.public.contracts import (
    AgentMessage,
    MessageType,
    RoleAgent,
    create_protocol_fsm,
)
from polaris.kernelone.contracts.technical import Result, TaggedError

logger = logging.getLogger(__name__)


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


class ReviewPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ReviewRecord:
    review_id: str
    task_id: str
    title: str
    priority: str = "medium"
    content: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    feedback: str = ""
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    reviewed_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "task_id": self.task_id,
            "title": self.title,
            "priority": self.priority,
            "content": self.content,
            "status": self.status.value,
            "feedback": self.feedback,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reviewed_by": self.reviewed_by,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReviewRecord:
        return cls(
            review_id=str(payload.get("review_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            title=str(payload.get("title") or ""),
            priority=str(payload.get("priority") or "medium"),
            content=str(payload.get("content") or ""),
            status=ReviewStatus(str(payload.get("status") or ReviewStatus.PENDING.value)),
            feedback=str(payload.get("feedback") or ""),
            issues=[str(item) for item in payload.get("issues", [])],
            suggestions=[str(item) for item in payload.get("suggestions", [])],
            created_at=str(payload.get("created_at") or datetime.now().isoformat()),
            updated_at=str(payload.get("updated_at") or datetime.now().isoformat()),
            reviewed_by=str(payload.get("reviewed_by") or ""),
        )


class QAAgent(RoleAgent):
    """QA role agent compatible with the current RoleAgent runtime."""

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, agent_name="QA")
        self._protocol_fsm: Any = None
        self._reviews: dict[str, ReviewRecord] = {}

    @property
    def protocol_fsm(self) -> Any:
        if self._protocol_fsm is None:
            self._protocol_fsm = create_protocol_fsm(self.workspace)
        return self._protocol_fsm

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register(
            "submit_review",
            self._tool_submit_review,
            description="Create a QA review request",
            parameters={
                "task_id": "Task identifier",
                "title": "Review title",
                "priority": "low|medium|high|critical",
                "content": "Optional review payload",
            },
        )
        tb.register(
            "approve_review",
            self._tool_approve_review,
            description="Approve an existing review",
            parameters={"review_id": "Review ID", "feedback": "Optional feedback"},
        )
        tb.register(
            "reject_review",
            self._tool_reject_review,
            description="Reject an existing review",
            parameters={
                "review_id": "Review ID",
                "reason": "Rejection reason",
                "issues": "Optional issue list",
            },
        )
        tb.register(
            "request_revision",
            self._tool_request_revision,
            description="Mark review as revision requested",
            parameters={
                "review_id": "Review ID",
                "feedback": "Revision guidance",
                "suggestions": "Optional suggestion list",
            },
        )
        tb.register(
            "get_review",
            self._tool_get_review,
            description="Get review details",
            parameters={"review_id": "Review ID"},
        )
        tb.register(
            "list_pending_reviews",
            self._tool_list_pending_reviews,
            description="List pending QA reviews",
            parameters={},
        )
        tb.register(
            "list_pending_approvals",
            self._tool_list_pending_approvals,
            description="List pending protocol approvals assigned to QA",
            parameters={},
        )
        tb.register(
            "approve_request",
            self._tool_approve_request,
            description="Approve protocol request",
            parameters={"request_id": "Request ID", "notes": "Approval notes"},
        )
        tb.register(
            "reject_request",
            self._tool_reject_request,
            description="Reject protocol request",
            parameters={"request_id": "Request ID", "reason": "Rejection reason"},
        )

    def _new_review_id(self) -> str:
        return f"review-{uuid4().hex[:8]}"

    def _persist_reviews_snapshot(self) -> None:
        self.memory.save_snapshot({"reviews": [record.to_dict() for record in self._reviews.values()]})

    def _mark_review(
        self,
        review_id: str,
        status: ReviewStatus,
        *,
        feedback: str = "",
        issues: list[str] | None = None,
        suggestions: list[str] | None = None,
    ) -> Result[ReviewRecord, TaggedError]:
        """Mark a review with a new status.

        Returns:
            Result containing the updated ReviewRecord on success
        """
        review = self._reviews.get(review_id)
        if not review:
            logger.warning("Review not found: %s", review_id)
            return Result.err(
                TaggedError("REVIEW_NOT_FOUND", f"Review {review_id} not found"),
            )

        review.status = status
        review.feedback = feedback
        review.reviewed_by = self.agent_name
        if issues is not None:
            review.issues = [str(item) for item in issues]
        if suggestions is not None:
            review.suggestions = [str(item) for item in suggestions]
        review.updated_at = datetime.now().isoformat()

        self.memory.append_history(
            {
                "action": "qa_review_update",
                "review_id": review_id,
                "status": review.status.value,
                "task_id": review.task_id,
            }
        )
        self._persist_reviews_snapshot()
        logger.info("Review %s marked as %s", review_id, status.value)
        return Result.ok(review)

    def _tool_submit_review(
        self,
        task_id: str,
        title: str,
        priority: str = "medium",
        content: str = "",
    ) -> dict[str, Any]:
        """Submit a new review request.

        Returns:
            Dict with ok=True and review data, or ok=False and error details
        """
        try:
            review = ReviewRecord(
                review_id=self._new_review_id(),
                task_id=str(task_id or "").strip(),
                title=str(title or "").strip() or "Untitled review",
                priority=str(priority or "medium"),
                content=str(content or ""),
            )
            self._reviews[review.review_id] = review

            self.memory.append_history(
                {
                    "action": "qa_review_created",
                    "review_id": review.review_id,
                    "task_id": review.task_id,
                    "title": review.title,
                }
            )
            self._persist_reviews_snapshot()

            logger.info("Review submitted: %s for task %s", review.review_id, review.task_id)
            return {"ok": True, "review": review.to_dict()}
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to submit review: %s", e)
            return {"ok": False, "error": str(e), "error_code": "INTERNAL_ERROR"}

    def _tool_approve_review(self, review_id: str, feedback: str = "") -> dict[str, Any]:
        """Approve a review by ID."""
        result = self._mark_review(
            str(review_id or "").strip(),
            ReviewStatus.APPROVED,
            feedback=str(feedback or ""),
        )
        if result.is_err and result.error is not None:
            err = result.error
            return {"ok": False, "error": err.message, "error_code": err.code}
        if result.value is not None:
            return {"ok": True, "review": result.value.to_dict()}
        return {"ok": False, "error": "Unexpected error", "error_code": "INTERNAL_ERROR"}

    def _tool_reject_review(
        self,
        review_id: str,
        reason: str,
        issues: list[str] | None = None,
    ) -> dict[str, Any]:
        """Reject a review by ID."""
        result = self._mark_review(
            str(review_id or "").strip(),
            ReviewStatus.REJECTED,
            feedback=str(reason or ""),
            issues=issues or [],
        )
        if result.is_err and result.error is not None:
            err = result.error
            return {"ok": False, "error": err.message, "error_code": err.code}
        if result.value is not None:
            return {"ok": True, "review": result.value.to_dict()}
        return {"ok": False, "error": "Unexpected error", "error_code": "INTERNAL_ERROR"}

    def _tool_request_revision(
        self,
        review_id: str,
        feedback: str,
        suggestions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Request revision for a review."""
        result = self._mark_review(
            str(review_id or "").strip(),
            ReviewStatus.REVISION_REQUESTED,
            feedback=str(feedback or ""),
            suggestions=suggestions or [],
        )
        if result.is_err and result.error is not None:
            err = result.error
            return {"ok": False, "error": err.message, "error_code": err.code}
        if result.value is not None:
            return {"ok": True, "review": result.value.to_dict()}
        return {"ok": False, "error": "Unexpected error", "error_code": "INTERNAL_ERROR"}

    def _tool_get_review(self, review_id: str) -> dict[str, Any]:
        """Get review details by ID."""
        review = self._reviews.get(str(review_id or "").strip())
        if not review:
            logger.warning("Review not found: %s", review_id)
            return {
                "ok": False,
                "error": f"Review {review_id} not found",
                "error_code": "REVIEW_NOT_FOUND",
            }
        return {"ok": True, "review": review.to_dict()}

    def _tool_list_pending_reviews(self) -> dict[str, Any]:
        """List all pending reviews."""
        pending = [record.to_dict() for record in self._reviews.values() if record.status == ReviewStatus.PENDING]
        return {"ok": True, "count": len(pending), "reviews": pending}

    def _tool_list_pending_approvals(self) -> dict[str, Any]:
        """List pending protocol approvals for QA."""
        try:
            pending = self.protocol_fsm.list_pending(to_role=self.agent_name)
            return {
                "ok": True,
                "count": len(pending),
                "requests": [
                    {
                        "request_id": request.request_id,
                        "protocol_type": request.protocol_type.value,
                        "from_role": request.from_role,
                        "to_role": request.to_role,
                        "content": request.content,
                        "created_at": request.created_at,
                    }
                    for request in pending
                ],
            }
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to list pending approvals: %s", e)
            return {
                "ok": False,
                "error": str(e),
                "error_code": "PROTOCOL_ERROR",
            }

    def _tool_approve_request(self, request_id: str, notes: str = "") -> dict[str, Any]:
        """Approve a protocol request."""
        try:
            ok = self.protocol_fsm.approve(
                str(request_id or "").strip(),
                approver=self.agent_name,
                notes=str(notes or ""),
            )
            if not ok:
                logger.warning("Failed to approve request: %s", request_id)
                return {
                    "ok": False,
                    "error": "Failed to approve request",
                    "error_code": "PROTOCOL_ERROR",
                }
            logger.info("Request approved: %s", request_id)
            return {"ok": True, "request_id": request_id, "status": "approved"}
        except (RuntimeError, ValueError) as e:
            logger.error("Error approving request %s: %s", request_id, e)
            return {
                "ok": False,
                "error": str(e),
                "error_code": "PROTOCOL_ERROR",
            }

    def _tool_reject_request(self, request_id: str, reason: str) -> dict[str, Any]:
        """Reject a protocol request."""
        try:
            ok = self.protocol_fsm.reject(
                str(request_id or "").strip(),
                rejecter=self.agent_name,
                reason=str(reason or ""),
            )
            if not ok:
                logger.warning("Failed to reject request: %s", request_id)
                return {
                    "ok": False,
                    "error": "Failed to reject request",
                    "error_code": "PROTOCOL_ERROR",
                }
            logger.info("Request rejected: %s", request_id)
            return {"ok": True, "request_id": request_id, "status": "rejected"}
        except (RuntimeError, ValueError) as e:
            logger.error("Error rejecting request %s: %s", request_id, e)
            return {
                "ok": False,
                "error": str(e),
                "error_code": "PROTOCOL_ERROR",
            }

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:  # type: ignore[override]
        """Handle incoming messages from other agents."""
        if message.type == MessageType.TASK:
            payload = message.payload if isinstance(message.payload, dict) else {}
            task_payload = payload.get("task", payload)
            task_id = str(task_payload.get("id") or payload.get("task_id") or "").strip()
            title = str(task_payload.get("title") or task_payload.get("subject") or "").strip()

            logger.info("Received TASK message for task_id: %s", task_id or "unknown")

            review_result = self._tool_submit_review(
                task_id=task_id or "unknown",
                title=title or "Task Review",
                content=str(task_payload.get("description") or ""),
            )
            return AgentMessage.create(
                msg_type=MessageType.RESULT,
                sender=self.agent_name,
                receiver=message.sender,
                payload={
                    "action": "review_submitted",
                    "task_id": task_id,
                    "review": review_result,
                },
                correlation_id=message.correlation_id,
            )

        if message.type == MessageType.COMMAND:
            payload = message.payload if isinstance(message.payload, dict) else {}
            command = str(payload.get("command") or "").strip().lower()
            if command == "get_status":
                return AgentMessage.create(
                    msg_type=MessageType.EVENT,
                    sender=self.agent_name,
                    receiver=message.sender,
                    payload={"status": self.get_status()},
                    correlation_id=message.correlation_id,
                )
        return None

    def run_cycle(self) -> bool:
        """Run one processing cycle."""
        message = self.message_queue.receive(block=False)
        if not message:
            return False
        response = self.handle_message(message)  # type: ignore[arg-type]
        if response is not None:
            self.message_queue.send(response)  # type: ignore[arg-type]
        return True

    def _load_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Load state from snapshot."""
        reviews = snapshot.get("reviews", [])
        if not isinstance(reviews, list):
            logger.warning("Invalid reviews snapshot format")
            return
        self._reviews = {}
        for item in reviews:
            if isinstance(item, dict) and item.get("review_id"):
                try:
                    record = ReviewRecord.from_dict(item)
                    self._reviews[record.review_id] = record
                except (RuntimeError, ValueError) as e:
                    logger.error("Failed to load review record: %s", e)

    def get_status(self) -> dict[str, Any]:
        """Get agent status."""
        status = super().get_status()
        status["reviews"] = {
            "total": len(self._reviews),
            "pending": len([record for record in self._reviews.values() if record.status == ReviewStatus.PENDING]),
            "approved": len([record for record in self._reviews.values() if record.status == ReviewStatus.APPROVED]),
            "rejected": len([record for record in self._reviews.values() if record.status == ReviewStatus.REJECTED]),
        }
        return status


class ReviewStore:
    """Compatibility wrapper around in-memory ReviewRecord storage."""

    def __init__(self, records: dict[str, ReviewRecord] | None = None) -> None:
        self._records = records if records is not None else {}

    def save(self, review: ReviewRecord) -> Result[None, TaggedError]:
        """Save a review record."""
        try:
            self._records[review.review_id] = review
            return Result.ok(None)
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to save review: %s", e)
            return Result.err(TaggedError("INTERNAL_ERROR", str(e)))

    def get(self, review_id: str) -> Result[ReviewRecord, TaggedError]:
        """Get a review by ID."""
        review = self._records.get(review_id)
        if not review:
            return Result.err(
                TaggedError("REVIEW_NOT_FOUND", f"Review {review_id} not found"),
            )
        return Result.ok(review)

    def get_by_task(self, task_id: str) -> Result[list[ReviewRecord], TaggedError]:
        """Get all reviews for a task."""
        try:
            reviews = [review for review in self._records.values() if review.task_id == task_id]
            return Result.ok(reviews)
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to get reviews by task: %s", e)
            return Result.err(TaggedError("INTERNAL_ERROR", str(e)))

    def get_pending(self) -> Result[list[ReviewRecord], TaggedError]:
        """Get all pending reviews."""
        try:
            reviews = [review for review in self._records.values() if review.status == ReviewStatus.PENDING]
            return Result.ok(reviews)
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to get pending reviews: %s", e)
            return Result.err(TaggedError("INTERNAL_ERROR", str(e)))

    def list_all(self) -> Result[list[ReviewRecord], TaggedError]:
        """List all reviews."""
        try:
            return Result.ok(list(self._records.values()))
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to list reviews: %s", e)
            return Result.err(TaggedError("INTERNAL_ERROR", str(e)))
