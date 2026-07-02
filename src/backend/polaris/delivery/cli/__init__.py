"""Canonical delivery CLI host exports.

Keep this package init lazy. ``python -m polaris.delivery.cli`` loads the
package before executing ``__main__``; eager importing the host here would
preload the module and trigger a runpy warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["create_parser", "main"]


def create_parser():
    from .__main__ import create_parser as _create_parser

    return _create_parser()


def main(argv: Sequence[str] | None = None) -> int:
    from .__main__ import main as _main

    return _main(argv)
