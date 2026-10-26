"""Lower public budget operations and old bytes require no role host."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_cold_public_budget_reserve_and_legacy_state_reopen_without_roles_runtime(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/budget_service_golden_v1.json"
    program = """
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'polaris.cells.roles.runtime' or fullname.startswith('polaris.cells.roles.runtime.'):
            raise ImportError('lower budget command must not load roles.runtime')
        return None
sys.meta_path.insert(0, BlockRuntime())
import polaris.cells.finops.budget_guard as root
import polaris.cells.finops.budget_guard.public as public
from polaris.cells.finops.budget_guard.public import service
from polaris.cells.finops.budget_guard.public.contracts import ReserveBudgetCommandV1
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
workspace = Path(sys.argv[1])
golden = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
fs = KernelFileSystem(str(workspace), get_default_adapter())
fs.write_text('runtime/state/budget/global.json', golden['serialized_state'], encoding='utf-8')
state = workspace / '.polaris/runtime/state/budget/global.json'
before = state.read_bytes()
budget = service.BudgetService(str(workspace))
assert budget.get_budget_status('task-a')['budgets'][0]['used'] == 400
assert budget.get_usage_stats(task_id='task-a')['totals'] == {'context_tokens':400}
assert state.read_bytes() == before
reserved = service.reserve_budget(ReserveBudgetCommandV1(
    scope_id='new-scope',workspace=str(workspace),role='architect',token_budget=11,
))
assert reserved.allowed and reserved.remaining_tokens == 11 and reserved.reason == 'reserved'
assert not any(name.startswith('polaris.cells.roles.runtime') for name in sys.modules)
for module in (root, public, service):
    assert not hasattr(module, 'CFOAgent'), 'old lower advisor ABI must not silently remain as a lazy role host'
assert importlib.util.find_spec('polaris.cells.finops.budget_guard.internal.budget_agent') is None
assert not (workspace / '.polaris/runtime/memory/CFO').exists()
"""
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program, str(tmp_path), str(fixture)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
