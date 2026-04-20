"""Context cache implementation."""

from .models import ContextPack


class ContextCache:
    def __init__(self) -> None:
        self._pack_cache: dict[str, ContextPack] = {}

    def get_cached_pack(self, request_hash: str) -> ContextPack | None:
        return self._pack_cache.get(request_hash)

    def cache_pack(self, pack: ContextPack) -> None:
        if pack and pack.request_hash:
            self._pack_cache[pack.request_hash] = pack
