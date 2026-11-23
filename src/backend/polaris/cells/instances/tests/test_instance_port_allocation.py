"""Auto-allocated instance ports must be reachable from a browser."""

from __future__ import annotations

from polaris.cells.instances.internal import service


def test_auto_allocation_skips_browser_unsafe_ports() -> None:
    # 6697 is Chromium's restricted list: a bench frontend allocated there
    # serves a blank page (net::ERR_UNSAFE_PORT) instead of the instance.
    for start in (6697, 6000, 6566, 10080):
        allocated = service.allocate_port(start)
        assert allocated not in service.BROWSER_UNSAFE_PORTS
        assert allocated > start


def test_auto_allocation_honours_an_unused_start_port() -> None:
    assert service.allocate_port(20000) == 20000


def test_auto_allocation_still_honours_excluded_ports() -> None:
    allocated = service.allocate_port(20010, excluded_ports={20010, 20011})
    assert allocated not in {20010, 20011}
    assert allocated >= 20012
