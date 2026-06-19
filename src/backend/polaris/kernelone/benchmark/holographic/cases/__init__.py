"""Holographic benchmark case executors, grouped by benchmark domain.

Each sibling module owns the executor functions plus the domain-private
helpers / fixtures for one benchmark domain. The dispatch table that maps
case IDs to executors is assembled in
``polaris.kernelone.benchmark.holographic.registry`` by importing every
module in this package.

This package is intentionally a namespace only; importers should depend on
the concrete domain modules or on ``registry.EXECUTORS`` rather than on a
re-export surface here.
"""

from __future__ import annotations
