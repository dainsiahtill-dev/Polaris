"""TaskRuntime history must remain readable without the Director executor."""

from __future__ import annotations

import os
import subprocess
import sys


def test_technical_effect_payload_imports_with_all_cells_unavailable() -> None:
    program = """
import importlib.abc
import sys

class BlockBusinessOwners(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == owner or fullname.startswith(owner + '.') for owner in (
            'polaris.cells', 'polaris.domain', 'polaris.application', 'polaris.delivery',
        )):
            raise ImportError('Cell and business owners are unavailable to technical effect payloads')
        return None

sys.meta_path.insert(0, BlockBusinessOwners())
from polaris.kernelone.effect.immutable_payload import (
    freeze_directed_effect_arguments, hash_directed_effect_arguments,
)
assert hash_directed_effect_arguments(freeze_directed_effect_arguments({'v': True})) == (
    '33dee8d53901cb0e9d1eef0a3a73206372146952719e4fc06b79a74bece4da72'
)
"""
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_task_effect_closure_imports_when_director_is_unavailable() -> None:
    """Catch reintroduction of executor imports into historical observation."""
    program = """
import importlib.abc
import sys

class BlockDirector(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'polaris.cells.director' or fullname.startswith('polaris.cells.director.'):
            raise ImportError('Director executor is unavailable to historical observation')
        return None

sys.meta_path.insert(0, BlockDirector())
from polaris.cells.runtime.task_runtime.internal.execution_effect_closure import (
    _negative_arguments_items,
    _negative_operation_binding_matches,
)
from polaris.cells.runtime.task_runtime.public.operation_identity import (
    DirectedEffectOperationSubjectV1,
    hash_directed_effect_operation_subject,
)
from types import SimpleNamespace
arguments = {'content': '汉🙂', 'file': 'src/a.txt'}
subject = DirectedEffectOperationSubjectV1(
    workspace='/workspace/旧', turn_id='turn', batch_id='batch', tool_call_id='call',
    inventory_ordinal=0, normalized_tool_name='write_file',
    normalized_arguments=_negative_arguments_items(arguments), effect_type='write',
    execution_mode='write_serial', prospective_operation_hash='a' * 64,
)
digest = hash_directed_effect_operation_subject(subject)
assert digest == '1f1af97c73e0b3894e0979a08cf2172b79be7fabcaabffef619c7e6e50f8d6fc'
member = SimpleNamespace(tool_call_id='call', ordinal=0, normalized_tool_name='write_file',
    effect_type='write', execution_mode='write_serial', intended_effect_fingerprint=digest,
    policy_verdict_hash=digest, expected_receipt_binding_hash=digest)
attempt = SimpleNamespace(workspace='/workspace/旧')
binding = SimpleNamespace(correlation=SimpleNamespace(turn_id='turn', batch_id='batch'))
assert _negative_operation_binding_matches(arguments, attempt, binding, member)
member.policy_verdict_hash = 'b' * 64
assert not _negative_operation_binding_matches(arguments, attempt, binding, member)
assert not any(name.startswith('polaris.cells.director.') for name in sys.modules)
"""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
