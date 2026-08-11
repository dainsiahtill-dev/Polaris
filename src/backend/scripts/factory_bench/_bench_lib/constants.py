"""Shared path anchors, fixtures, and module-level constants.

Private helper module for run_factory_bench.
"""

from __future__ import annotations

# ruff: noqa: F401
# Imports are re-exported via namespace pull to sibling concern modules.
import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlparse

from polaris.cells.factory.pipeline.internal.bench_gates import (
    CANONICAL_BENCH_PROJECTION_SOURCE,
    LEGACY_BENCH_ARTIFACT_SOURCE,
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_canonical_bench_projection,
    build_llm_route_audit,
    build_real_run_gate,
    collect_llm_events,
    resolve_expected_llm_bindings,
)
from polaris.cells.factory.pipeline.public.service import (
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_projection,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.benchmark.factory_audit import (
    aggregate_factory_audits,
    build_factory_audit_record,
)
from polaris.kernelone.benchmark.factory_depth_contract import (
    build_factory_bench_level_contract,
    format_level_contract_for_requirements,
)
from polaris.kernelone.platform_modules.residual_attribution import (
    build_factory_audits_attribution_pack,
)
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots
from scripts.factory_bench.backend_fingerprint import (
    build_run_backend_metadata,
    check_backend_freshness,
    compute_source_fingerprint,
    resolve_backend_source_root,
)
from scripts.factory_bench.factory_http_client import (
    _http_post_json as _shared_http_post_json,
    cancel_factory_run,
    get_audit_bundle,
    list_factory_runs,
    retry_factory_run_from_director,
    start_factory_run,
    wait_run_until_terminal,
)

_logger = logging.getLogger(__name__)

# Directory anchors: this file lives in scripts/factory_bench/_bench_lib/
_FACTORY_BENCH_DIR = Path(__file__).resolve().parent.parent
_MODULE_BACKEND_ROOT = _FACTORY_BENCH_DIR.parents[1]
_FIXTURE = _FACTORY_BENCH_DIR / "projects_v2.json"
_BACKEND_ROOT = resolve_backend_source_root(_MODULE_BACKEND_ROOT)
_REPO_ROOT = _BACKEND_ROOT.parent.parent
FACTORY_BENCH_REQUIRED_LLM_ROLES = ("pm", "chief_engineer", "director", "qa")
_LAUNCHER_INSTANCE_MODES = {"observed", "isolated"}
_BENCH_SESSION_REPORTING_MODES = {"auto", "shared", "off"}
