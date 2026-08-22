from __future__ import annotations

from polaris.cells.roles.adapters.internal.director import execute_method as execute_method_module


def test_quality_repair_progress_rejects_obvious_algebraic_semantic_noop() -> None:
    before = "func settle(b *Body) {\n    b.Velocity.Y = 0\n}\n"
    after = "func settle(b *Body) {\n    b.Velocity.Y = 0\n    b.Velocity.Y -= 0\n}\n"

    evidence = execute_method_module._quality_repair_progress_evidence(
        before_files={"engine/engine.go": before},
        after_files={"engine/engine.go": after},
        before_errors=["engine_test.go:112: still moving downward at rest vy=98.1"],
        after_errors=["engine_test.go:112: still moving downward at rest vy=98.1"],
        before_missing_count=0,
        after_missing_count=0,
        successful_write_paths=["engine/engine.go"],
    )

    assert evidence["physical_workspace_mutation_evidenced"] is True
    assert evidence["workspace_mutation_evidenced"] is False
    assert evidence["semantic_noop_detected"] is True
    assert evidence["semantic_noop_paths"] == ["engine/engine.go"]
    assert evidence["effective_progress"] is False


def test_quality_repair_progress_allows_unseen_verifier_forward_unmask_once() -> None:
    before_errors = [
        "--- FAIL: TestClampsOnFloor (0.00s)\n    floor_test.go:10: still moving downward",
        "--- FAIL: TestEndToEnd (0.00s)\n    main_test.go:20: still moving downward",
    ]
    after_errors = [
        "--- FAIL: TestAppliesGravity (0.00s)\n    engine_test.go:30: wrong velocity",
        "--- FAIL: TestRestitutionBounces (0.00s)\n    engine_test.go:40: expected bounce",
    ]
    after_signature = execute_method_module._artifact_quality_error_signature(after_errors)

    advanced = execute_method_module._quality_repair_progress_evidence(
        before_files={"engine.go": "old"},
        after_files={"engine.go": "meaningfully changed"},
        before_errors=before_errors,
        after_errors=after_errors,
        before_missing_count=0,
        after_missing_count=0,
        successful_write_paths=["engine.go"],
        previously_seen_error_signatures={
            execute_method_module._artifact_quality_error_signature(before_errors)
        },
    )
    assert advanced["effective_progress"] is True
    assert advanced["progress_kind"] == "forward_unmask"
    assert advanced["forward_unmask_advances"] is True

    cycle = execute_method_module._quality_repair_progress_evidence(
        before_files={"engine.go": "old"},
        after_files={"engine.go": "meaningfully changed"},
        before_errors=before_errors,
        after_errors=after_errors,
        before_missing_count=0,
        after_missing_count=0,
        successful_write_paths=["engine.go"],
        previously_seen_error_signatures={after_signature},
    )
    assert cycle["effective_progress"] is False
    assert cycle["forward_unmask_candidate"] is True
    assert cycle["forward_unmask_advances"] is False
