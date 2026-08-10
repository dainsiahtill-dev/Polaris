"""StressEngine composition — public engine class."""

# mypy: ignore-errors

from ._artifacts import _StressEngineArtifactsMixin
from ._code_audit import _StressEngineCodeAuditMixin
from ._core import _StressEngineCoreMixin
from ._execution import _StressEngineExecutionMixin
from ._gates import _StressEngineGatesMixin
from ._reporting import _StressEngineReportingMixin
from ._stages import _StressEngineStagesMixin


class StressEngine(
    _StressEngineCoreMixin,
    _StressEngineExecutionMixin,
    _StressEngineArtifactsMixin,
    _StressEngineGatesMixin,
    _StressEngineStagesMixin,
    _StressEngineReportingMixin,
    _StressEngineCodeAuditMixin,
):
    """压测引擎 - 纯 HTTP API 驱动

    只使用 Polaris 对外暴露的 HTTP API：
    - /settings                 - 配置 workspace
    - /v2/factory/runs          - 创建/查询 Factory 运行
    - /v2/director/tasks        - 任务状态查询
    - /v2/factory/runs/{id}/events - 运行时事件
    """
