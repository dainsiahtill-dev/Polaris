"""Minimal Linux pidfd bridge; missing Python wrappers never imply kernel support.

Only absent wrappers use the process libc's named symbols. Kernel denials from
either backend propagate unchanged; callers own and must close returned FDs.
"""

from __future__ import annotations

import ctypes
import errno
import operator
import os
import signal
import sys
from functools import lru_cache
from typing import Any

_stdlib_pidfd_open = getattr(os, "pidfd_open", None)
_stdlib_pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)


@lru_cache(maxsize=1)
def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _libc_function(name: str, argtypes: list[type[Any]]) -> Any:
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "Linux pidfd required")
    try:
        function = getattr(_libc(), name)
    except (OSError, AttributeError) as exc:
        raise OSError(errno.ENOSYS, f"libc {name} unavailable") from exc
    function.argtypes = argtypes
    function.restype = ctypes.c_int
    return function


def _c_int(value: int) -> int:
    number = operator.index(value)
    bound = 1 << (ctypes.sizeof(ctypes.c_int) * 8 - 1)
    if not -bound <= number < bound:
        raise OverflowError("pidfd argument does not fit C int")
    return number


def _check_result(result: int) -> int:
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def pidfd_open(pid: int) -> int:
    """Open a real close-on-exec pidfd with flags=0, or propagate OS failure."""
    pid = _c_int(pid)
    if _stdlib_pidfd_open is not None:
        return int(_stdlib_pidfd_open(pid, 0))
    function = _libc_function("pidfd_open", [ctypes.c_int, ctypes.c_uint])
    return _check_result(int(function(pid, 0)))


def pidfd_send_signal(descriptor: int, sig: int) -> None:
    """Signal the descriptor's incarnation with siginfo=NULL and flags=0."""
    descriptor, sig = _c_int(descriptor), _c_int(sig)
    if _stdlib_pidfd_send_signal is not None:
        _stdlib_pidfd_send_signal(descriptor, sig, None, 0)
        return
    function = _libc_function("pidfd_send_signal", [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint])
    _check_result(int(function(descriptor, sig, None, 0)))
