"""Encoding and environment utilities for polaris Loop."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import MutableMapping
from functools import lru_cache

logger = logging.getLogger(__name__)

#: UTF-8 locale names in preference order.  The first one the host actually
#: provides is used; ``C`` is the POSIX fallback that always exists.
_UTF8_LOCALE_CANDIDATES: tuple[str, ...] = (
    "C.UTF-8",
    "C.utf8",
    "en_US.UTF-8",
    "en_US.utf8",
)


@lru_cache(maxsize=1)
def _host_locales() -> frozenset[str]:
    """The locale names this host provides.

    An empty result means the host could not be probed; the caller then
    trusts the requested locale instead of rejecting it.
    """
    try:
        completed = subprocess.run(
            ["locale", "-a"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Could not list host locales: %s", exc)
        return frozenset()
    if completed.returncode != 0:
        return frozenset()
    return frozenset(line.strip() for line in completed.stdout.splitlines() if line.strip())


def is_locale_available(name: str) -> bool:
    """Whether the host provides *name*.

    Comparison ignores the ``-``/``_``/case spelling differences between
    ``C.UTF-8`` and ``C.utf8``.
    """
    available = _host_locales()
    if not available:
        # The host cannot be probed: do not reject a locale on a guess.
        return True
    wanted = name.replace("-", "").replace("_", "").lower()
    return any(entry.replace("-", "").replace("_", "").lower() == wanted for entry in available)


@lru_cache(maxsize=1)
def resolve_utf8_locale() -> str:
    """Return a UTF-8 locale this host can actually use.

    Tools print ``setlocale: LC_ALL: cannot change locale (...)`` on stderr
    when the requested locale is missing.  The platform records command stderr
    as physical error evidence, so injecting an unavailable locale corrupts
    the evidence of every command.  Prefer the host's own UTF-8 locale, then
    the first available UTF-8 candidate, and fall back to the always-present
    POSIX ``C`` locale.
    """
    for candidate in (os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if candidate and "UTF-8" in candidate.upper() and is_locale_available(candidate):
            return candidate
    for candidate in _UTF8_LOCALE_CANDIDATES:
        if is_locale_available(candidate):
            return candidate
    return "C"


def apply_utf8_locale(env: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """Give *env* a UTF-8 locale this host can actually use.

    A locale the host cannot provide is replaced: an unusable locale makes
    every child tool warn on stderr, which the platform would record as a
    command failure.  A usable host locale is kept.
    """
    resolved = resolve_utf8_locale()
    for name in ("LANG", "LC_ALL", "LC_CTYPE"):
        current = env.get(name)
        if current and is_locale_available(current):
            continue
        env[name] = resolved
    return env


def enforce_utf8() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    apply_utf8_locale(os.environ)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to reconfigure stdout: {e}")
    try:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to reconfigure stderr: {e}")


def build_utf8_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    apply_utf8_locale(env)
    if extra:
        env.update(extra)
    return env
