"""AgentStressRunner composition — public runner class."""

# mypy: ignore-errors

from ._core import _AgentStressRunnerCoreMixin
from ._diagnostics import _AgentStressRunnerDiagnosticsMixin
from ._io import _AgentStressRunnerIOMixin
from ._lifecycle import _AgentStressRunnerLifecycleMixin
from ._reporting import _AgentStressRunnerReportingMixin


class AgentStressRunner(
    _AgentStressRunnerCoreMixin,
    _AgentStressRunnerLifecycleMixin,
    _AgentStressRunnerDiagnosticsMixin,
    _AgentStressRunnerReportingMixin,
    _AgentStressRunnerIOMixin,
):
    """AI Agent 专项压测运行器"""
