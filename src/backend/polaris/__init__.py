"""Polaris target architecture package root."""

# ─── Early env-var normalization (must run before any polaris.kernelone import)
# Any import of ``polaris`` or any sub-package triggers this, ensuring the
# anti-corruption layer is active for CLI entry points, server startup, and
# test collection alike.
try:
    from polaris._env_compat import normalize_env_prefix

    normalize_env_prefix()
except ImportError:
    # Compat module missing — acceptable, kernel will see unset env vars.
    pass
except Exception as exc:  # noqa: BLE001
    # Unexpected error during normalization — log but don't block import.
    import logging

    logging.getLogger(__name__).warning("env compat normalization failed: %s", exc)
