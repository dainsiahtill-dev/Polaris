"""Low configuration public imports and persistence do not load role actors."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter

CHILD = r"""
import importlib, importlib.abc, json, sys
class NoRoles(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'polaris.cells.roles.runtime' or fullname.startswith('polaris.cells.roles.runtime.'):
            raise RuntimeError('unexpected_roles_runtime:' + fullname)
sys.meta_path.insert(0, NoRoles())
module = importlib.import_module(sys.argv[1])
from polaris.cells.llm.control_plane.public.service import LLMConfigService, LLMConfigStore
from polaris.cells.llm.control_plane.internal import llm_config_agent as core
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
core.KernelFileSystem = lambda _inferred, adapter: KernelFileSystem(sys.argv[2], adapter)
service = LLMConfigService(config_dir=sys.argv[3])
if sys.argv[4] == 'write':
    result = service.set_llm_config('pm', 'fixture', 'cli', 'model', 'profile', {'api_key':'synthetic-key','command':'codex fixture'})
    assert result['config']['provider_cfg']['api_key'] == '***'
else:
    config = LLMConfigStore(sys.argv[3]).get('pm')
    assert config.provider_cfg['api_key'] == 'synthetic-key'
    assert config.provider_kind == 'codex'
assert not any(name == 'polaris.cells.roles.runtime' or name.startswith('polaris.cells.roles.runtime.') for name in sys.modules)
print(json.dumps({'role':'pm','phase':sys.argv[4],'module':sys.argv[1]}))
"""


@pytest.mark.parametrize(
    "module",
    [
        "polaris.cells.llm.control_plane.public.service",
        "polaris.cells.llm.control_plane.public",
        "polaris.cells.llm.control_plane",
        "polaris.cells.llm.control_plane.internal.llm_config_agent",
    ],
)
def test_low_public_producer_and_cold_reader_with_role_runtime_blocked(tmp_path: Path, module: str) -> None:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[5]),
            "KERNELONE_HOME": str(tmp_path / "private-home"),
            "KERNELONE_STATE_TO_RAMDISK": "0",
            "KERNELONE_RUNTIME_ROOT": "",
            "KERNELONE_RUNTIME_CACHE_ROOT": "",
            "KERNELONE_RAMDISK_ROOT": "",
        }
    )
    config_dir = tmp_path / "private-home/config/llm"
    before_read = None
    for phase in ("write", "read"):
        result = subprocess.run(
            [sys.executable, "-c", CHILD, module, str(tmp_path), str(config_dir), phase],
            cwd=tmp_path,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert f'"phase": "{phase}"' in result.stdout
        encoded = get_default_adapter().read_bytes(str(config_dir / "configs.json"))
        if phase == "write":
            before_read = encoded
        else:
            assert encoded == before_read
