"""实时投影系统。

WebSocket runtime.v2(JetStream) 连接与事件归一化。

This package is the lossless successor of the former ``projection`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...observer.projection`` and ``from ...observer.projection import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / third-party names that
# were module-level attributes of the former ``projection`` module. Keeping
# them bound here preserves the exact importable attribute surface after the split.
import asyncio
import contextlib
import json
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots
from polaris.tests.agent_stress.observer.projection._lifecycle import RuntimeProjection

logger = logging.getLogger("observer.projection")
