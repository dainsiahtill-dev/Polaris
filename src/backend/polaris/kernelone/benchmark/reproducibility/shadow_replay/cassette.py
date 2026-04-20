"""Cassette data structures for Chronos Mirror.

Defines the on-disk format for recorded HTTP interactions.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Current cassette format version
CASSETTE_VERSION = "2.0"

# Max preview length for truncated display
MAX_PREVIEW_LENGTH = 500


@dataclass
class HTTPRequest:
    """Recorded HTTP request details."""

    method: str
    url: str
    headers: dict[str, str]
    body_hash: str
    body_preview: str | None = None
    # Full body stored as base64 for binary safety, None if too large
    body: str | None = None

    @classmethod
    def from_raw(
        cls,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        max_body_size: int = 1_000_000,  # 1MB limit
    ) -> HTTPRequest:
        """Create from raw httpx request data.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL
            headers: Request headers
            body: Request body bytes
            max_body_size: Maximum body size to store fully (truncate above)

        Returns:
            HTTPRequest with body stored (full if small, truncated if large)
        """
        body_hash = ""
        body_preview = None
        body_b64 = None

        if body:
            body_hash = hashlib.sha256(body).hexdigest()[:32]
            # Store preview (truncated)
            try:
                body_preview = body[:MAX_PREVIEW_LENGTH].decode("utf-8", errors="replace")
            except (RuntimeError, ValueError):
                body_preview = f"<binary {len(body)} bytes>"

            # Store full body if small enough
            if len(body) <= max_body_size:
                body_b64 = base64.b64encode(body).decode("ascii")

        return cls(
            method=method.upper(),
            url=url,
            headers=dict(headers),  # Copy to avoid mutation
            body_hash=body_hash,
            body_preview=body_preview,
            body=body_b64,
        )

    def get_body_bytes(self) -> bytes | None:
        """Decode stored body back to bytes.

        Returns:
            Original body bytes or None
        """
        if self.body is None:
            return None
        return base64.b64decode(self.body.encode("ascii"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HTTPRequest:
        return cls(**data)


@dataclass
class HTTPResponse:
    """Recorded HTTP response details."""

    status_code: int
    headers: dict[str, str]
    body_hash: str
    body_preview: str | None = None
    # Full body stored as base64 for binary safety, None if too large
    body: str | None = None
    tokens_used: int | None = None

    @classmethod
    def from_raw(
        cls,
        status_code: int,
        headers: dict[str, str],
        body: bytes | None,
        tokens_used: int | None = None,
        max_body_size: int = 10_000_000,  # 10MB limit for responses
    ) -> HTTPResponse:
        """Create from raw httpx response data.

        Args:
            status_code: HTTP status code
            headers: Response headers
            body: Response body bytes
            tokens_used: Optional token count (for LLM calls)
            max_body_size: Maximum body size to store fully

        Returns:
            HTTPResponse with body stored (full if small, truncated if large)
        """
        body_hash = ""
        body_preview = None
        body_b64 = None

        if body:
            body_hash = hashlib.sha256(body).hexdigest()[:32]
            try:
                body_preview = body[:MAX_PREVIEW_LENGTH].decode("utf-8", errors="replace")
            except (RuntimeError, ValueError):
                body_preview = f"<binary {len(body)} bytes>"

            # Store full body if small enough
            if len(body) <= max_body_size:
                body_b64 = base64.b64encode(body).decode("ascii")

        return cls(
            status_code=status_code,
            headers=dict(headers),
            body_hash=body_hash,
            body_preview=body_preview,
            body=body_b64,
            tokens_used=tokens_used,
        )

    def get_body_bytes(self) -> bytes | None:
        """Decode stored body back to bytes.

        Returns:
            Original body bytes or None
        """
        if self.body is None:
            return None
        return base64.b64decode(self.body.encode("ascii"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HTTPResponse:
        return cls(**data)


@dataclass
class CassetteEntry:
    """Single request-response pair in a cassette."""

    sequence: int
    timestamp: str
    request: HTTPRequest
    response: HTTPResponse
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "request": self.request.to_dict(),
            "response": self.response.to_dict(),
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CassetteEntry:
        return cls(
            sequence=data["sequence"],
            timestamp=data["timestamp"],
            request=HTTPRequest.from_dict(data["request"]),
            response=HTTPResponse.from_dict(data["response"]),
            latency_ms=data.get("latency_ms"),
        )


@dataclass
class CassetteFormat:
    """Complete cassette file format."""

    cassette_id: str
    created_at: str
    mode: str  # "record" | "replay" | "both"
    version: str = CASSETTE_VERSION
    entries: list[CassetteEntry] = field(default_factory=list)
    sanitized: bool = True
    sanitizer_version: str = "1.0"

    def add_entry(self, entry: CassetteEntry) -> None:
        """Add an entry to the cassette.

        Args:
            entry: CassetteEntry to add
        """
        self.entries.append(entry)

    def find_entry(
        self,
        method: str,
        url: str,
        body_hash: str | None = None,
    ) -> CassetteEntry | None:
        """Find a matching entry in the cassette.

        Args:
            method: HTTP method
            url: Full URL
            body_hash: Optional request body hash for precise matching

        Returns:
            Matching CassetteEntry or None if not found
        """
        for entry in self.entries:
            if entry.request.method != method.upper():
                continue
            if entry.request.url != url:
                continue
            if body_hash and entry.request.body_hash != body_hash:
                continue
            return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cassette_id": self.cassette_id,
            "created_at": self.created_at,
            "mode": self.mode,
            "version": self.version,
            "sanitized": self.sanitized,
            "sanitizer_version": self.sanitizer_version,
        }

    def _header_dict(self) -> dict[str, Any]:
        """Get header dict without entries (for JSONL header line)."""
        return {
            "cassette_id": self.cassette_id,
            "created_at": self.created_at,
            "mode": self.mode,
            "version": self.version,
            "sanitized": self.sanitized,
            "sanitizer_version": self.sanitizer_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CassetteFormat:
        return cls(
            cassette_id=data["cassette_id"],
            created_at=data["created_at"],
            mode=data["mode"],
            version=data.get("version", CASSETTE_VERSION),
            entries=[CassetteEntry.from_dict(e) for e in data.get("entries", [])],
            sanitized=data.get("sanitized", True),
            sanitizer_version=data.get("sanitizer_version", "1.0"),
        )

    def to_jsonl(self) -> str:
        """Serialize to JSON-Lines format (one entry per line).

        Format:
        - Line 1: Header (cassette metadata, NO entries)
        - Line 2+: Individual entries

        Returns:
            JSON string with header and entries as separate lines
        """
        lines = [json.dumps(self._header_dict(), separators=(",", ":"))]
        for entry in self.entries:
            lines.append(json.dumps(entry.to_dict(), separators=(",", ":")))
        return "\n".join(lines)

    @classmethod
    def from_jsonl(cls, content: str) -> CassetteFormat:
        """Deserialize from JSON-Lines format.

        Args:
            content: JSON-Lines content

        Returns:
            CassetteFormat instance
        """
        lines = content.strip().split("\n")
        if not lines:
            raise ValueError("Empty JSON-Lines content")

        # First line is cassette header
        cassette = CassetteFormat.from_dict(json.loads(lines[0]))

        # Subsequent lines are entries
        for line in lines[1:]:
            if line.strip():
                cassette.add_entry(CassetteEntry.from_dict(json.loads(line)))

        return cassette


@dataclass
class Cassette:
    """High-level cassette interface with file persistence."""

    cassette_id: str
    cassette_dir: Path
    mode: str = "both"
    _format: CassetteFormat | None = field(default=None, repr=False)
    _sanitizer: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.cassette_dir, str):
            self.cassette_dir = Path(self.cassette_dir)
        self.cassette_dir.mkdir(parents=True, exist_ok=True)

    def _get_sanitizer(self) -> Any:
        """Lazy-load sanitizer to avoid circular imports."""
        if self._sanitizer is None:
            from polaris.kernelone.benchmark.reproducibility.shadow_replay.sanitization import (
                get_sanitizer,
            )

            self._sanitizer = get_sanitizer()
        return self._sanitizer

    @property
    def format(self) -> CassetteFormat:
        """Get or create cassette format."""
        if self._format is None:
            self._format = CassetteFormat(
                cassette_id=self.cassette_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                mode=self.mode,
            )
        return self._format

    @property
    def path(self) -> Path:
        """Get the cassette file path."""
        return self.cassette_dir / f"{self.cassette_id}.jsonl"

    def load(self) -> CassetteFormat:
        """Load cassette from disk.

        Returns:
            CassetteFormat instance

        Raises:
            FileNotFoundError: If cassette file doesn't exist
        """
        if not self.path.exists():
            raise FileNotFoundError(f"Cassette not found: {self.path}")

        content = self.path.read_text(encoding="utf-8")
        self._format = CassetteFormat.from_jsonl(content)
        return self._format

    def save(self) -> None:
        """Save cassette to disk with sanitization.

        Sanitizes all entries before writing to remove sensitive data.
        """
        if self._format is None:
            return

        # Apply sanitization to each entry
        sanitizer = self._get_sanitizer()
        sanitized_entries: list[dict[str, Any]] = []

        for entry in self._format.entries:
            entry_dict = entry.to_dict()
            # Sanitize the entry
            sanitized = sanitizer.sanitize(entry_dict)
            sanitized_entries.append(sanitized)

        # Create sanitized format for saving
        sanitized_format = CassetteFormat(
            cassette_id=self._format.cassette_id,
            created_at=self._format.created_at,
            mode=self._format.mode,
            version=self._format.version,
            entries=[],  # Will be set below
            sanitized=True,
            sanitizer_version=self._format.sanitizer_version,
        )

        # Rebuild entries from sanitized dicts
        for sanitized_entry in sanitized_entries:
            sanitized_format.add_entry(CassetteEntry.from_dict(sanitized_entry))

        # Write to disk
        self.path.write_text(sanitized_format.to_jsonl(), encoding="utf-8")

    def exists(self) -> bool:
        """Check if cassette exists on disk."""
        return self.path.exists()

    def add_entry(
        self,
        request: HTTPRequest,
        response: HTTPResponse,
        latency_ms: float | None = None,
    ) -> CassetteEntry:
        """Add a request-response entry to the cassette.

        Args:
            request: HTTP request details
            response: HTTP response details
            latency_ms: Optional latency measurement

        Returns:
            Created CassetteEntry
        """
        entry = CassetteEntry(
            sequence=len(self.format.entries),
            timestamp=datetime.now(timezone.utc).isoformat(),
            request=request,
            response=response,
            latency_ms=latency_ms,
        )
        self.format.add_entry(entry)
        return entry

    def find_entry(
        self,
        method: str,
        url: str,
        body_hash: str | None = None,
    ) -> CassetteEntry | None:
        """Find a matching entry in the loaded cassette.

        Args:
            method: HTTP method
            url: Full URL
            body_hash: Optional body hash for precise matching

        Returns:
            Matching entry or None
        """
        return self.format.find_entry(method, url, body_hash)

    def clear(self) -> None:
        """Clear all entries from the cassette."""
        self._format = CassetteFormat(
            cassette_id=self.cassette_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            mode=self.mode,
        )
