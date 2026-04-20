"""Tests for polaris.cells.audit.evidence.bundle_service

Focus: exception-observability contract for _get_current_commit —
git failures must:
1. Return the sentinel string "sha-unknown" (not "unknown", not empty)
2. Emit a warning log with exc_info so the traceback is preserved

All subprocess calls are mocked; no real git or filesystem access.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from polaris.cells.audit.evidence.bundle_service import EvidenceBundleService

# ---------------------------------------------------------------------------
# _get_current_commit: command executor raises an exception
# ---------------------------------------------------------------------------

class TestGetCurrentCommitException:
    def test_returns_sha_unknown_on_exception(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.side_effect = RuntimeError("git not found")

            with caplog.at_level(logging.WARNING):
                result = svc._get_current_commit(str(tmp_path))

        assert result == "sha-unknown", (
            "Must return sentinel 'sha-unknown', not 'unknown' or empty string"
        )

    def test_logs_warning_with_exc_info_on_exception(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.side_effect = OSError("permission denied")

            with caplog.at_level(logging.WARNING):
                svc._get_current_commit(str(tmp_path))

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, "At least one warning must be emitted"
        assert warning_records[0].exc_info is not None, (
            "exc_info must be attached so the traceback is preserved in logs"
        )

    def test_logs_workspace_path_in_warning(self, tmp_path, caplog):
        svc = EvidenceBundleService()
        workspace_str = str(tmp_path)

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.side_effect = RuntimeError("boom")

            with caplog.at_level(logging.WARNING):
                svc._get_current_commit(workspace_str)

        # The workspace path must appear in at least one warning message
        # so operators can locate which workspace triggered the failure
        combined = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert workspace_str in combined, (
            "Warning log must include the workspace path for operator traceability"
        )


# ---------------------------------------------------------------------------
# _get_current_commit: command returns non-zero exit code
# ---------------------------------------------------------------------------

class TestGetCurrentCommitNonZeroExit:
    def _make_failed_result(self):
        return {"ok": False, "returncode": 128, "stdout": "", "stderr": "not a git repo"}

    def test_returns_sha_unknown_on_nonzero_exit(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.return_value = self._make_failed_result()

            with caplog.at_level(logging.WARNING):
                result = svc._get_current_commit(str(tmp_path))

        assert result == "sha-unknown"

    def test_logs_warning_on_nonzero_exit(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.return_value = self._make_failed_result()

            with caplog.at_level(logging.WARNING):
                svc._get_current_commit(str(tmp_path))

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "A warning must be logged when git rev-parse exits non-zero"
        )


# ---------------------------------------------------------------------------
# _get_current_commit: command returns empty stdout
# ---------------------------------------------------------------------------

class TestGetCurrentCommitEmptyStdout:
    def test_returns_sha_unknown_on_empty_stdout(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.return_value = {"ok": True, "returncode": 0, "stdout": "   "}

            with caplog.at_level(logging.WARNING):
                result = svc._get_current_commit(str(tmp_path))

        assert result == "sha-unknown", (
            "Empty/whitespace-only stdout must also yield 'sha-unknown'"
        )


# ---------------------------------------------------------------------------
# _get_current_commit: happy path (sanity check)
# ---------------------------------------------------------------------------

class TestGetCurrentCommitSuccess:
    def test_returns_sha_on_success(self, tmp_path):
        svc = EvidenceBundleService()
        expected_sha = "abc123def456abc123def456abc123def456abc1"

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.return_value = {
                "ok": True,
                "returncode": 0,
                "stdout": expected_sha + "\n",
            }

            result = svc._get_current_commit(str(tmp_path))

        assert result == expected_sha, "Must return stripped SHA on success"


# ---------------------------------------------------------------------------
# Sentinel value contract: "sha-unknown" is detectable, "unknown" is not
# ---------------------------------------------------------------------------

class TestSentinelContract:
    """Verify the sentinel is the specific string 'sha-unknown'.

    The contract is: callers and audit tools can detect an unreliable baseline
    by checking `bundle.base_sha == "sha-unknown"`.  The old value "unknown"
    was too generic and could be confused with a user-supplied value.
    """

    def test_sentinel_is_not_generic_unknown(self, tmp_path, caplog):
        svc = EvidenceBundleService()

        with patch(
            "polaris.cells.audit.evidence.bundle_service.CommandExecutionService"
        ) as MockCmdSvc:
            instance = MockCmdSvc.return_value
            instance.run.side_effect = RuntimeError("any failure")

            with caplog.at_level(logging.WARNING):
                result = svc._get_current_commit(str(tmp_path))

        assert result != "unknown", (
            "Sentinel must NOT be the ambiguous string 'unknown'; "
            "it must be 'sha-unknown' to be clearly detectable"
        )
        assert result == "sha-unknown"
