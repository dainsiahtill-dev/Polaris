"""Audit Quick - 极简命令行接口 (增强版)

此文件为 Facade，重定向到重构后的模块结构。
实际实现位于 polaris/delivery/cli/audit/audit/ 目录下。

用法:
    python audit_quick.py verify
    python audit_quick.py stats
    python audit_quick.py events --discover
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        pass
    else:
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)


if __name__ == "__main__":
    from polaris.delivery.cli.audit.audit.cli import main

    main()
