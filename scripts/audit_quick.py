#!/usr/bin/env python3
"""Compatibility entrypoint for backend audit_quick CLI.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_backend_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    backend_root = repo_root / "src" / "backend"
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


_bootstrap_backend_path()

from polaris.cells.audit.diagnosis.public import ErrorChainSearcher  # noqa: E402
from polaris.delivery.cli.audit.audit.auditor import export_data as _export_data  # noqa: E402
from polaris.delivery.cli.audit.audit.cli import main as _backend_main  # noqa: E402
from polaris.delivery.cli.audit.audit.diagnosis import diagnose_runtime as _diagnose_runtime  # noqa: E402
from polaris.delivery.cli.audit.audit.factory_ops import collect_factory_events as _collect_factory_events  # noqa: E402
from polaris.delivery.cli.audit.audit.file_ops import (  # noqa: E402
    collect_runtime_event_inventory as _collect_runtime_event_inventory,
)
from polaris.delivery.cli.audit.audit.formatters import resolve_export_format as _resolve_export_format  # noqa: E402
from polaris.delivery.cli.audit.audit_agent import verify  # noqa: E402

__all__ = [
    "ErrorChainSearcher",
    "_collect_factory_events",
    "_collect_runtime_event_inventory",
    "_diagnose_runtime",
    "_export_data",
    "_resolve_export_format",
    "main",
    "verify",
]


def main() -> None:
    """Run the canonical backend audit CLI through the legacy script path.

    Tests and third-party callers historically monkeypatch symbols on this
    module. Before delegating, mirror those symbols into the canonical handler
    modules so the compatibility contract remains observable.
    """
    from polaris.delivery.cli.audit.audit import handlers, handlers_advanced

    handlers.verify = verify
    handlers_advanced.ErrorChainSearcher = ErrorChainSearcher
    _backend_main()


if __name__ == "__main__":
    main()
