"""Tests for polaris.cells.audit.verdict.internal.review_gate."""

from __future__ import annotations

from polaris.cells.audit.verdict.internal.review_gate import (
    CodeChange,
    Review,
    ReviewEventType,
    ReviewGate,
    create_review_gate,
    get_review_gate,
)


class TestReviewGate:
    """ReviewGate integration tests."""

    def test_create_code_change(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        assert change.task_id == "task-1"
        assert change.worker_id == "worker-1"
        assert change.file_path == "src/main.py"
        assert change.status == "generated"

    def test_request_review_updates_change_status(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None
        assert review.task_id == "task-1"
        assert review.status == "reviewing"

        updated_change = gate.get_code_change(change.change_id)
        assert updated_change is not None
        assert updated_change.status == "reviewing"

    def test_request_review_nonexistent_change_returns_none(self) -> None:
        gate = ReviewGate()
        review = gate.request_review("nonexistent-change-id")
        assert review is None

    def test_approve_review(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None

        approved = gate.approve_review(review.review_id, reviewer="alice")
        assert approved is not None
        assert approved.verdict == "approved"
        assert approved.reviewer == "alice"
        assert approved.status == "completed"

        updated_change = gate.get_code_change(change.change_id)
        assert updated_change is not None
        assert updated_change.status == "approved"

    def test_reject_review(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None

        rejected = gate.reject_review(review.review_id, reviewer="bob", comments=[{"line": 10, "text": "bad"}])
        assert rejected is not None
        assert rejected.verdict == "rejected"
        assert rejected.comments == [{"line": 10, "text": "bad"}]

        updated_change = gate.get_code_change(change.change_id)
        assert updated_change is not None
        assert updated_change.status == "rejected"

    def test_add_comments(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None

        gate.add_comments(review.review_id, [{"line": 1, "text": "comment 1"}])
        gate.add_comments(review.review_id, [{"line": 2, "text": "comment 2"}])

        updated = gate.get_review(review.review_id)
        assert updated is not None
        assert len(updated.comments) == 2

    def test_can_complete_task_no_changes(self) -> None:
        gate = ReviewGate()
        # No changes created, should allow completion
        assert gate.can_complete_task("task-without-changes") is True

    def test_can_complete_task_with_approved_change(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None
        gate.approve_review(review.review_id)

        assert gate.can_complete_task("task-1") is True

    def test_can_complete_task_with_pending_change(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        gate.request_review(change.change_id)
        # Don't approve

        assert gate.can_complete_task("task-1") is False

    def test_can_complete_task_with_rejected_change(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        review = gate.request_review(change.change_id)
        assert review is not None
        gate.reject_review(review.review_id)

        assert gate.can_complete_task("task-1") is False

    def test_get_task_review_status(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="src/main.py",
            base_sha="abc123",
            head_sha="def456",
        )
        assert gate.get_task_review_status("task-1") == "generated"

        review = gate.request_review(change.change_id)
        assert review is not None
        assert gate.get_task_review_status("task-1") == "reviewing"

        gate.approve_review(review.review_id)
        assert gate.get_task_review_status("task-1") == "approved"

    def test_get_task_review_status_nonexistent(self) -> None:
        gate = ReviewGate()
        assert gate.get_task_review_status("nonexistent-task") is None

    def test_get_reviews_by_status(self) -> None:
        gate = ReviewGate()

        # Create two changes
        change1 = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="a.py",
            base_sha="abc123",
            head_sha="def456",
        )
        change2 = gate.create_code_change(
            task_id="task-2",
            worker_id="worker-1",
            file_path="b.py",
            base_sha="abc123",
            head_sha="def456",
        )

        review1 = gate.request_review(change1.change_id)
        assert review1 is not None
        gate.request_review(change2.change_id)

        gate.approve_review(review1.review_id)

        # Reviews start as "reviewing", not "pending"
        reviewing = gate.get_reviews_by_status("reviewing")
        assert len(reviewing) == 1

        completed = gate.get_reviews_by_status("completed")
        assert len(completed) == 1

    def test_get_pending_reviews(self) -> None:
        gate = ReviewGate()
        change = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="a.py",
            base_sha="abc123",
            head_sha="def456",
        )
        gate.request_review(change.change_id)

        # Reviews start as "reviewing", get_pending_reviews filters by "pending"
        # which won't match since reviews are created with "reviewing" status
        pending = gate.get_pending_reviews()
        # No reviews have status "pending" by default
        assert len(pending) == 0

    def test_get_all_changes(self) -> None:
        gate = ReviewGate()
        gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="a.py",
            base_sha="abc123",
            head_sha="def456",
        )
        gate.create_code_change(
            task_id="task-2",
            worker_id="worker-1",
            file_path="b.py",
            base_sha="abc123",
            head_sha="def456",
        )

        all_changes = gate.get_all_changes()
        assert len(all_changes) == 2

    def test_get_all_reviews(self) -> None:
        gate = ReviewGate()
        change1 = gate.create_code_change(
            task_id="task-1",
            worker_id="worker-1",
            file_path="a.py",
            base_sha="abc123",
            head_sha="def456",
        )
        gate.create_code_change(
            task_id="task-2",
            worker_id="worker-1",
            file_path="b.py",
            base_sha="abc123",
            head_sha="def456",
        )
        gate.request_review(change1.change_id)

        all_reviews = gate.get_all_reviews()
        assert len(all_reviews) == 1

    def test_create_review_gate_factory(self) -> None:
        gate = create_review_gate()
        assert isinstance(gate, ReviewGate)
        # Each call creates a new instance
        gate2 = create_review_gate()
        assert gate is not gate2

    def test_get_review_gate_singleton(self) -> None:
        gate1 = get_review_gate()
        gate2 = get_review_gate()
        # Should return the same singleton
        assert gate1 is gate2


class TestCodeChange:
    """CodeChange dataclass tests."""

    def test_to_dict(self) -> None:
        change = CodeChange(
            change_id="c1",
            task_id="t1",
            worker_id="w1",
            file_path="src/main.py",
            base_sha="abc",
            head_sha="def",
            status="approved",
        )
        result = change.to_dict()
        assert result["change_id"] == "c1"
        assert result["task_id"] == "t1"
        assert result["status"] == "approved"


class TestReview:
    """Review dataclass tests."""

    def test_to_dict(self) -> None:
        review = Review(
            review_id="r1",
            change_id="c1",
            task_id="t1",
            worker_id="w1",
            verdict="approved",
            reviewer="alice",
            status="completed",
        )
        result = review.to_dict()
        assert result["review_id"] == "r1"
        assert result["verdict"] == "approved"
        assert result["reviewer"] == "alice"


class TestReviewEventType:
    """ReviewEventType enum tests."""

    def test_all_event_types_defined(self) -> None:
        assert ReviewEventType.DIFF_GENERATED is not None
        assert ReviewEventType.REVIEW_REQUESTED is not None
        assert ReviewEventType.REVIEW_STARTED is not None
        assert ReviewEventType.REVIEW_APPROVED is not None
        assert ReviewEventType.REVIEW_REJECTED is not None
        assert ReviewEventType.REVIEW_COMMENTS is not None

    def test_event_types_are_auto(self) -> None:
        # All should be auto() values
        assert isinstance(ReviewEventType.DIFF_GENERATED.value, int)
