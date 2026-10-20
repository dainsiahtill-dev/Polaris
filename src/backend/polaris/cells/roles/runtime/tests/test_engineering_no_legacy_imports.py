"""Fresh imports must not reconstruct retired run dependencies."""

from __future__ import annotations

import os
import subprocess
import sys


def test_new_service_types_and_store_import_without_loading_legacy_engine():
    script = """
import importlib.abc
import sys
blocked = (
 'polaris.cells.roles.runtime.internal.agent_runtime_base',
 'polaris.cells.roles.runtime.internal.protocol_fsm',
 'polaris.cells.roles.runtime.public.contracts._legacy_exports',
 'polaris.cells.roles.runtime.public.contracts._execution_contracts',
 'polaris.cells.roles.runtime.public.contracts._object_contracts',
 'polaris.cells.roles.runtime.public.service',
 'polaris.cells.runtime.task_runtime',
 'polaris.cells.roles.session',
 'polaris.cells.roles.kernel',
 'polaris.cells.director.execution',
 'polaris.cells.runtime.run_ledger',
 'polaris.cells.runtime.job_token',
 'polaris.cells.runtime.execution_broker',
 'polaris.cells.audit.run_ledger',
)
class Sentinel(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
   raise RuntimeError('legacy import blocked: ' + fullname)
sys.meta_path.insert(0, Sentinel())
from polaris.cells.roles.runtime.public import EngineeringRunService, RoleRunCommand, PermissionScope, RunLimits, ToolObservation, RoleRunResult
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService as DirectEngineeringRunService
from polaris.cells.roles.runtime.public.contracts import RoleRunCommand as SameCommand
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
assert SameCommand is RoleRunCommand
assert EngineeringRunService is DirectEngineeringRunService
assert EngineeringRunService.__module__ == 'polaris.cells.roles.runtime.public.engineering_service'
assert not any(any(name == prefix or name.startswith(prefix + '.') for prefix in blocked) for name in sys.modules)
print('legacy sentinel untouched')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env=os.environ.copy()
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "legacy sentinel untouched"


def test_lazy_legacy_exports_preserve_aliases_and_private_backcompat():
    script = """
import sys
from polaris.cells.roles.runtime.public import contracts
assert 'polaris.cells.roles.runtime.public.contracts._legacy_exports' not in sys.modules
from polaris.cells.roles.runtime.public.contracts import RoleRunCommand
assert 'polaris.cells.roles.runtime.public.contracts._legacy_exports' not in sys.modules
legacy = contracts.ExecuteRoleTaskCommandV1
assert legacy.__name__ == 'ExecuteRoleTaskCommandV1'
assert contracts.hashlib.sha256(b'x').hexdigest().startswith('2d7116')
assert contracts.Mapping is not None
assert callable(contracts._normalize_history)
assert contracts.RoleAgent is not None
assert contracts.create_protocol_fsm is not None
assert contracts.ExecuteRoleTaskCommandV1 is legacy
assert 'RoleRunCommand' in dir(contracts)
print('legacy compatibility retained')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env=os.environ.copy()
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "legacy compatibility retained"
