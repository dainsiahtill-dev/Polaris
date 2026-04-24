"""HTTP utilities for LLM providers.

SSRF validation is provided by validate_base_url_for_ssrf().
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


def normalize_base_url(raw: str, default: str = "") -> str:
    base = str(raw or default or "").strip()
    return base.rstrip("/")


def join_url(base_url: str, path: str, strip_prefixes: Iterable[str] | None = None) -> str:
    if not base_url:
        return path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    if strip_prefixes:
        normalized_base = base_url.rstrip("/")
        for prefix in strip_prefixes:
            if not prefix:
                continue
            normalized_prefix = prefix if prefix.startswith("/") else f"/{prefix}"
            if normalized_base.endswith(normalized_prefix.rstrip("/")) and path.startswith(f"{normalized_prefix}/"):
                path = path[len(normalized_prefix) :]
                break
    return base_url + path


def merge_headers(base: dict[str, str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, str]:
    headers: dict[str, str] = dict(base or {})
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is None:
                continue
            headers[str(key)] = str(value)
    return headers


# ----------------------------------------------------------------------
# SSRF protection
# ----------------------------------------------------------------------

# RFC 1918 private blocks + link-local + loopback (CIDR notation)
_BLOCKED_NETWORKS: list[tuple[str, int]] = [
    ("10.0.0.0", 8),  # RFC 1918: 10.0.0.0/8
    ("172.16.0.0", 12),  # RFC 1918: 172.16.0.0/12
    ("192.168.0.0", 16),  # RFC 1918: 192.168.0.0/16
    ("169.254.0.0", 16),  # RFC 3927: link-local (AWS metadata 169.254.169.254)
    ("127.0.0.0", 8),  # RFC 1122: loopback
]


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if *ip_str* resolves inside a blocked private/link-local range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        for start, prefix_len in _BLOCKED_NETWORKS:
            network = ipaddress.ip_network(f"{start}/{prefix_len}", strict=False)
            if ip in network:
                return True
    except ValueError:
        pass
    return False


def validate_base_url_for_ssrf(base_url: str, allow_localhost: bool = False) -> tuple[bool, str]:
    """Validate that *base_url* does not point to an internal network (SSRF guard).

    Args:
        base_url: URL to validate.
        allow_localhost: If True, localhost/127.0.0.1/::1 are permitted (development use).

    Returns:
        (True, "") when the URL is safe.
        (False, "<reason>") when the URL points to a blocked address or has an
        invalid / dangerous scheme.
    """
    if not base_url:
        return False, "base_url cannot be empty"

    stripped = base_url.strip()
    lower_stripped = stripped.lower()

    # Scheme check
    if lower_stripped.startswith("http://"):
        if not allow_localhost:
            return False, ("http:// scheme is not allowed for external endpoints; use https://")
        # http:// is only permitted when it points to localhost explicitly
        host = stripped[7:].split("/")[0]
        if host not in ("localhost", "127.0.0.1", "::1"):
            return False, ("http:// is only allowed for localhost; use https://")
        return True, ""
    if not lower_stripped.startswith("https://"):
        return False, "Invalid URL scheme; only https:// is allowed"

    # Parse hostname
    try:
        parsed = urlparse(stripped if stripped.startswith("http") else f"https://{stripped}")
        host = parsed.hostname or ""
        if not host:
            return False, "Cannot extract hostname from base_url"

        # localhost exception
        if host in ("localhost", "127.0.0.1", "::1"):
            if not allow_localhost:
                return False, "localhost is not allowed for external endpoints"
            return True, ""

        # Blocked IP check
        if _is_blocked_ip(host):
            return False, (f"Host '{host}' resolves to a blocked internal network address")

        # Resolve hostname to IP(s) and check each resolved address
        try:
            addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _family, _, _, _, sockaddr in addr_info:
                resolved_ip = sockaddr[0]
                if isinstance(resolved_ip, str) and _is_blocked_ip(resolved_ip):
                    return False, (
                        f"Host '{host}' resolves to {resolved_ip}, which is a blocked internal network address"
                    )
        except socket.gaierror:
            # Cannot resolve — skip IP check; scheme check already passed
            pass
    except (RuntimeError, ValueError) as e:
        return False, f"Failed to parse base_url: {e}"

    return True, ""
