"""Tests for Resident EvidenceBundle integration (Phase 1.1).

验证:
1. DecisionRecord 自动创建 EvidenceBundle
2. evidence_bundle_id 正确关联
3. affected_files/affected_symbols 自动填充
"""

import os
import tempfile
import unittest
from pathlib import Path

from polaris.cells.resident.autonomy.public.service import (
    get_resident_service,
)


class TestResidentEvidenceIntegration(unittest.TestCase):
    """Test ResidentService + EvidenceBundleService integration."""

    def setUp(self):
        """Create temporary workspace for each test."""
        self.temp_dir = tempfile.mkdtemp(prefix="resident_test_")
        self.workspace = self.temp_dir

        # Initialize git repo (required for EvidenceBundle)
        os.chdir(self.workspace)
        os.system('git init -q')
        os.system('git config user.email "test@test.com"')
        os.system('git config user.name "Test"')

        # Create a test file and commit
        test_file = Path(self.workspace) / "test.py"
        test_file.write_text("print('hello')")
        os.system('git add .')
        os.system('git commit -q -m "initial"')

        # Reset service cache to get fresh instance
        from polaris.cells.resident.autonomy.internal.resident_runtime_service import reset_resident_services
        reset_resident_services()

        # Get service instance
        self.service = get_resident_service(self.workspace)

    def tearDown(self):
        """Clean up temporary workspace."""
        import shutil
        # Return to original directory
        os.chdir("C:\\Users\\dains\\Documents\\GitLab\\polaris")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Reset service cache
        from polaris.cells.resident.autonomy.internal.resident_runtime_service import reset_resident_services
        reset_resident_services()

    def test_record_decision_auto_creates_evidence_bundle(self):
        """Test that recording a decision auto-creates EvidenceBundle."""
        # Modify a file to create working tree changes
        test_file = Path(self.workspace) / "test.py"
        test_file.write_text("print('hello world')\n# new line")

        # Record a decision
        decision = self.service.record_decision({
            "actor": "director",
            "stage": "test_stage",
            "summary": "Test decision",
            "verdict": "success",
        })

        # Verify EvidenceBundle was created
        self.assertIsNotNone(decision.evidence_bundle_id)
        self.assertIsInstance(decision.evidence_bundle_id, str)
        self.assertTrue(len(decision.evidence_bundle_id) > 0)

        # Verify affected_files was populated
        self.assertIsInstance(decision.affected_files, list)
        self.assertEqual(decision.affected_files, ["test.py"])

        print(f"✅ Decision created with bundle_id: {decision.evidence_bundle_id}")
        print(f"✅ Affected files: {decision.affected_files}")

    def test_get_decision_evidence_bundle(self):
        """Test retrieving EvidenceBundle for a decision."""
        # Modify a file
        test_file = Path(self.workspace) / "test.py"
        test_file.write_text("def main():\n    pass")

        # Record decision
        decision = self.service.record_decision({
            "actor": "director",
            "stage": "coding",
            "summary": "Add main function",
            "verdict": "success",
        })

        # Retrieve evidence bundle
        result = self.service.get_decision_evidence_bundle(decision.decision_id)

        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result["decision_id"], decision.decision_id)
        self.assertEqual(result["evidence_bundle_id"], decision.evidence_bundle_id)
        self.assertIn("bundle", result)

        bundle_data = result["bundle"]
        self.assertEqual(bundle_data["workspace"], self.workspace)
        self.assertEqual(bundle_data["source_type"], "manual")

        print(f"✅ Evidence bundle retrieved: {bundle_data['bundle_id']}")
        print(f"✅ Changes: {len(bundle_data['change_set'])} files")

    def test_decision_without_changes_no_bundle_failure(self):
        """Test that clean working tree doesn't break decision recording."""
        # No changes in working tree

        decision = self.service.record_decision({
            "actor": "resident",
            "stage": "planning",
            "summary": "Planning decision",
            "verdict": "success",
        })

        # Decision should still be recorded
        self.assertIsNotNone(decision.decision_id)

        # Evidence bundle may or may not be created (depends on implementation)
        # but it shouldn't throw an error
        print(f"✅ Decision recorded without errors: {decision.decision_id}")

    def test_runtime_state_includes_bundle_id(self):
        """Test that runtime state includes evidence_bundle_id in last_summary."""
        test_file = Path(self.workspace) / "test.py"
        test_file.write_text("# modified")

        decision = self.service.record_decision({
            "actor": "director",
            "stage": "implementation",
            "summary": "Implementation",
            "verdict": "success",
        })

        # Check runtime state
        status = self.service.get_status(include_details=False)
        runtime = status.get("runtime", {})
        last_summary = runtime.get("last_summary", {})

        self.assertEqual(last_summary.get("evidence_bundle_id"), decision.evidence_bundle_id)
        print("✅ Runtime state includes evidence_bundle_id")


if __name__ == "__main__":
    unittest.main(verbosity=2)

