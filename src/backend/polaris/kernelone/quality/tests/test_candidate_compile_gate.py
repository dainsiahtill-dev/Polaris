"""Regression tests for workspace-aware candidate compile protection."""

from pathlib import Path

from polaris.kernelone.quality.candidate_compile_gate import check_candidate_workspace_compile


def test_rejects_candidate_that_expands_existing_go_diagnostics(tmp_path: Path) -> None:
    """A red baseline is not permission to add more compiler failures.

    Exact L3-22 r47 started with two Go diagnostics.  A Director edit changed a
    public return signature and expanded the verifier residual across callers,
    but the old gate skipped the candidate shadow whenever the baseline was
    already red.  The harmful edit was therefore committed and repeatedly
    projected as a successful physical effect.
    """

    (tmp_path / "go.mod").write_text("module example.com/candidate\n\ngo 1.21\n", encoding="utf-8")
    broken = tmp_path / "broken" / "broken.go"
    broken.parent.mkdir(parents=True)
    broken.write_text("package broken\n\nfunc Existing() int { return missing }\n", encoding="utf-8")
    target = tmp_path / "engine" / "engine.go"
    target.parent.mkdir(parents=True)
    original = "package engine\n\nfunc Value() int {\n\tvalue := 1\n\treturn value\n}\n"
    target.write_text(original, encoding="utf-8")
    candidate = "package engine\n\nfunc Value() int {\n\treturn value\n}\n"

    result = check_candidate_workspace_compile(tmp_path, "engine/engine.go", candidate)

    assert result.checked is True
    assert result.before_ok is False
    assert result.after_ok is False
    assert result.regression is True
    assert "undefined: value" in result.error


def test_allows_candidate_that_reduces_existing_go_diagnostics(tmp_path: Path) -> None:
    """An incomplete project remains repairable when diagnostics decrease."""

    (tmp_path / "go.mod").write_text("module example.com/candidate\n\ngo 1.21\n", encoding="utf-8")
    target = tmp_path / "engine" / "engine.go"
    target.parent.mkdir(parents=True)
    target.write_text(
        "package engine\n\nfunc First() int { return missingOne }\nfunc Second() int { return missingTwo }\n",
        encoding="utf-8",
    )
    candidate = (
        "package engine\n\nfunc First() int { return 1 }\nfunc Second() int { return missingTwo }\n"
    )

    result = check_candidate_workspace_compile(tmp_path, "engine/engine.go", candidate)

    assert result.checked is True
    assert result.before_ok is False
    assert result.after_ok is False
    assert result.regression is False


def test_rejects_rust_candidate_that_breaks_compile_green_workspace(tmp_path: Path) -> None:
    """A Rust quality repair must not commit a newly invented API call.

    Exact L3-23 r19 was compile-green before QA repair.  The forced edit then
    inserted ``PatienceTracker::add`` even though that method did not exist,
    leaving three E0599 diagnostics on disk.  The candidate compile gate must
    detect that regression in a shadow crate before the Director tool commits
    the physical edit.
    """

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "candidate-rust"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "lib.rs"
    target.parent.mkdir(parents=True)
    target.write_text(
        "pub struct Tracker;\n\nimpl Tracker {\n    pub fn len(&self) -> usize { 0 }\n}\n",
        encoding="utf-8",
    )
    candidate = (
        "pub struct Tracker;\n\n"
        "impl Tracker {\n    pub fn len(&self) -> usize { 0 }\n}\n\n"
        "pub fn seed(tracker: &Tracker) { tracker.add(); }\n"
    )

    result = check_candidate_workspace_compile(tmp_path, "src/lib.rs", candidate)

    assert result.checked is True
    assert result.before_ok is True
    assert result.after_ok is False
    assert result.regression is True
    assert result.command[:2] == ("cargo", "check")
    assert "no method named `add`" in result.error
