from __future__ import annotations

from polaris.delivery.http.routers.docs import _sync_plan_to_runtime
from polaris.kernelone.storage.io_paths import build_cache_root


def test_plan_sync_no_plan_source_is_noop(tmp_path) -> None:
    workspace = str(tmp_path)
    cache_root = build_cache_root("", workspace)
    _sync_plan_to_runtime(workspace, cache_root)
