"""Regression: StreamingPatchBuffer must concatenate stream chunks verbatim.

Finding 4: ``_feed_buffering`` / ``flush`` reconstructed the buffered patch with
``"\\n".join(self._buffer)``. Stream chunks split at arbitrary mid-line (even
mid-token) boundaries, so joining with a newline inserts characters the model
never emitted, corrupting SEARCH/REPLACE bodies and the ``=======`` divider. The
single-chunk path (``_feed_normal``) uses plain concatenation, so the two paths
disagreed. Both must use ``"".join`` to match the real byte stream.
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.streaming_patch_buffer import (
    PatchBlock,
    StreamingPatchBuffer,
)

_PATCH = "foo.py\n<<<<<<< SEARCH\ndef calc():\n    return 1\n=======\ndef calc():\n    return 2\n>>>>>>> REPLACE\n"


def _blocks(stream: StreamingPatchBuffer, chunks: list[str]) -> list[PatchBlock]:
    collected: list[PatchBlock] = []
    for chunk in chunks:
        _visible, blocks = stream.feed(chunk)
        collected.extend(blocks)
    return collected


def _signature(blocks: list[PatchBlock]) -> list[tuple[str, str, str]]:
    return [(b.path, b.search, b.replace) for b in blocks]


def test_split_mid_token_matches_single_chunk() -> None:
    """A patch split mid-token across two chunks reconstructs byte-identically
    to the single-chunk feed (no injected newline corrupts the divider/body)."""
    single = _blocks(StreamingPatchBuffer("/tmp/ws"), [_PATCH])

    mid = len(_PATCH) // 2
    # Confirm the split lands inside the divider token, the worst case.
    assert _PATCH[mid - 1 : mid + 1] != "\n\n"
    two = _blocks(StreamingPatchBuffer("/tmp/ws"), [_PATCH[:mid], _PATCH[mid:]])

    assert _signature(single) == _signature(two)
    assert _signature(two) == [("foo.py", "def calc():\n    return 1\n", "def calc():\n    return 2\n")]


def test_search_body_has_no_injected_newline() -> None:
    """Splitting inside the SEARCH body must not inject a newline into it."""
    # Split right inside "return 1".
    marker = "return "
    idx = _PATCH.index(marker) + len(marker)
    blocks = _blocks(StreamingPatchBuffer("/tmp/ws"), [_PATCH[:idx], _PATCH[idx:]])
    assert len(blocks) == 1
    assert blocks[0].search == "def calc():\n    return 1\n"
    assert blocks[0].replace == "def calc():\n    return 2\n"
