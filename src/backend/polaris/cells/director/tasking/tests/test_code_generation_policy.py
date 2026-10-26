"""Private policy-helper unit coverage belongs to its Tasking owner."""

from __future__ import annotations

import pytest
from polaris.cells.director.tasking.internal.code_generation_engine import _raise_policy_violation
from polaris.cells.director.tasking.public import CodeGenerationPolicyViolationError


class TestCodeGenerationPolicyViolationError:
    def test_raise_policy_violation(self):
        with pytest.raises(CodeGenerationPolicyViolationError) as exc_info:
            _raise_policy_violation("test_action")
        assert "test_action" in str(exc_info.value)
