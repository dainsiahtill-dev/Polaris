"""ShadowReplay: Non-invasive HTTP recording and replay context manager.

Provides session-level HTTP interception via async context manager.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    CassetteNotFoundError,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
    HTTPExchange,
    apply_http_patch,
    clear_interceptor,
    remove_http_patch,
    set_interceptor,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.player import (
    ShadowPlayer,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.recorder import (
    ShadowRecorder,
)

logger = logging.getLogger(__name__)


class ShadowReplay:
    """Non-invasive HTTP recording and replay via async context manager.

    Modes:
        - "record": Only record new responses
        - "replay": Only replay existing recordings, fail if missing
        - "both": Record new and replay existing (default)

    Usage:
        async with ShadowReplay(cassette_id="task-123", mode="both") as replay:
            # All httpx.AsyncClient calls are intercepted
            result = await call_llm_api(prompt)  # Recorded
            result = await call_llm_api(prompt)  # Replayed

        # Cassette is automatically saved on exit
    """

    def __init__(
        self,
        cassette_id: str,
        mode: str = "both",
        cassette_dir: str | Path | None = None,
        strict: bool = True,
        auto_save: bool = True,
    ) -> None:
        """Initialize ShadowReplay.

        Args:
            cassette_id: Unique identifier for this cassette
            mode: Operation mode ("record" | "replay" | "both")
            cassette_dir: Directory to store cassettes. Defaults to temp dir
            strict: If True, replay mode raises error on missing entries
            auto_save: If True, save cassette after each entry
        """
        self.cassette_id = cassette_id
        self.mode = mode
        self.strict = strict
        self.auto_save = auto_save

        # Default cassette dir
        if cassette_dir is None:
            import tempfile

            self.cassette_dir = Path(tempfile.gettempdir()) / "shadow_replay"
        else:
            self.cassette_dir = Path(cassette_dir)

        # State
        self._cassette: Cassette | None = None
        self._recorder: ShadowRecorder | None = None
        self._player: ShadowPlayer | None = None
        self._patched = False

        # Validate mode
        valid_modes = {"record", "replay", "both"}
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")

    @property
    def cassette(self) -> Cassette:
        """Get the cassette instance."""
        if self._cassette is None:
            self._cassette = Cassette(
                cassette_id=self.cassette_id,
                cassette_dir=self.cassette_dir,
                mode=self.mode,
            )
        return self._cassette

    async def __aenter__(self) -> ShadowReplay:
        """Enter the ShadowReplay context.

        Returns:
            Self
        """
        logger.info(
            "[ShadowReplay] Entering context: cassette_id=%s, mode=%s",
            self.cassette_id,
            self.mode,
        )

        # Load or create cassette
        if self.mode in ("replay", "both"):
            if self.cassette.exists():
                self.cassette.load()
                logger.info(
                    "[ShadowReplay] Loaded cassette with %d entries",
                    len(self.cassette.format.entries),
                )
            elif self.mode == "replay":
                raise CassetteNotFoundError(
                    cassette_id=self.cassette_id,
                    cassette_dir=str(self.cassette_dir),
                )

        # Apply HTTP patch
        await apply_http_patch()
        self._patched = True

        # Set up interceptor based on mode
        if self.mode == "record":
            self._recorder = ShadowRecorder(
                cassette=self.cassette,
                auto_save=self.auto_save,
            )
            await self._recorder.start()
            set_interceptor(self._recorder.intercept)

        elif self.mode == "replay":
            self._player = ShadowPlayer(
                cassette=self.cassette,
                strict=self.strict,
            )
            await self._player.start()
            set_interceptor(self._player.intercept)

        else:  # "both"
            # In "both" mode, try replay first, fall back to record
            self._recorder = ShadowRecorder(
                cassette=self.cassette,
                auto_save=self.auto_save,
            )
            self._player = ShadowPlayer(
                cassette=self.cassette,
                strict=False,  # Non-strict in both mode
            )
            await self._recorder.start()
            await self._player.start()

            # Combined interceptor that tries replay first, then records
            # Note: in "both" mode, both _player and _recorder are always set
            recorder = self._recorder
            player = self._player

            async def combined_intercept(exchange: HTTPExchange) -> tuple[bool, Any]:
                # Try player first
                entry = self.cassette.find_entry(
                    method=exchange.method,
                    url=exchange.url,
                )
                if entry is not None:
                    # Replay found - short circuit
                    return await player.intercept(exchange)  # type: ignore[union-attr]
                # No replay found - proceed with recording
                return await recorder.intercept(exchange)  # type: ignore[union-attr]

            set_interceptor(combined_intercept)

        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit the ShadowReplay context."""
        logger.info(
            "[ShadowReplay] Exiting context: cassette_id=%s, mode=%s",
            self.cassette_id,
            self.mode,
        )

        # Clear interceptor
        clear_interceptor()

        # Stop recorder/player
        if self._recorder is not None:
            await self._recorder.stop()
        if self._player is not None:
            await self._player.stop()

        # Save cassette
        if self._cassette is not None and self._cassette.format.entries:
            self._cassette.save()
            logger.info(
                "[ShadowReplay] Saved cassette with %d entries",
                len(self._cassette.format.entries),
            )

        # Remove HTTP patch
        if self._patched:
            await remove_http_patch()
            self._patched = False

    @property
    def entry_count(self) -> int:
        """Number of entries in the cassette."""
        if self._cassette is None:
            return 0
        return len(self._cassette.format.entries)

    def clear(self) -> None:
        """Clear all entries from the cassette."""
        if self._cassette is not None:
            self._cassette.clear()
