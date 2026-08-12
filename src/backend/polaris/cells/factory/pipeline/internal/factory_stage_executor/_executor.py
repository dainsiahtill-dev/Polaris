"""OrchestrationStageExecutor composition (lossless package split)."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from ._mixin_00 import _Mixin00
from ._mixin_01 import _Mixin01
from ._mixin_02 import _Mixin02
from ._mixin_03 import _Mixin03
from ._mixin_04 import _Mixin04


class OrchestrationStageExecutor(_Mixin00, _Mixin01, _Mixin02, _Mixin03, _Mixin04):
    """Production executor backed by OrchestrationCommandService."""
