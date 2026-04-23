"""Polaris target architecture package root."""

# ─── Early env-var normalization (must run before any polaris.kernelone import)
# Any import of ``polaris`` or any sub-package triggers this, ensuring the
# anti-corruption layer is active for CLI entry points, server startup, and
# test collection alike.
try:
    from polaris._env_compat import normalize_env_prefix

    normalize_env_prefix()
except Exception:
    # If the compat module is missing or broken, do not block package import.
    # The error will surface naturally when code tries to read unset env vars.
    pass
