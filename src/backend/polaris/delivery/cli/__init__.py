"""Canonical delivery CLI host exports.

Keep this package init lazy. ``python -m polaris.delivery.cli.polaris_cli``
loads the package before executing the target module; eager importing
``polaris_cli`` here would preload the module and trigger a runpy warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["create_parser", "main"]


def create_parser():
    from .polaris_cli import create_parser as _create_parser

    return _create_parser()


def main(argv: Sequence[str] | None = None) -> int:
    from .polaris_cli import main as _main

    return _main(argv)
