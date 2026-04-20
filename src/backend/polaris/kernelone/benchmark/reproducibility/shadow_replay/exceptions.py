"""Chronos Mirror exceptions."""


class ShadowReplayError(Exception):
    """Base exception for ShadowReplay errors."""

    pass


class CassetteNotFoundError(ShadowReplayError):
    """Raised when cassette file does not exist in replay mode."""

    def __init__(self, cassette_id: str, cassette_dir: str) -> None:
        self.cassette_id = cassette_id
        self.cassette_dir = cassette_dir
        super().__init__(f"Cassette '{cassette_id}' not found in '{cassette_dir}'. Use record mode first to create it.")


class UnrecordedRequestError(ShadowReplayError):
    """Raised when a request has no recording in replay mode.

    This prevents silent failures during replay - if a request wasn't
    recorded, we fail fast rather than making a live call that could
    produce non-deterministic results.
    """

    def __init__(
        self,
        method: str,
        url: str,
        body_hash: str | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.body_hash = body_hash
        body_info = f" (body_hash={body_hash})" if body_hash else ""
        super().__init__(
            f"Unrecorded request: {method} {url}{body_info}. Use record mode to capture this request first."
        )


class CassetteFormatError(ShadowReplayError):
    """Raised when cassette format is invalid."""

    pass


class SanitizationError(ShadowReplayError):
    """Raised when sanitization fails."""

    pass
