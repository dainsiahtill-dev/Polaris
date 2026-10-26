"""SLM binding port for the ContextOS summarizer tier.

The KernelOne foundation must not depend on a business Cell.  The SLM
summarizer therefore consumes only the narrow contracts declared here and in
:mod:`polaris.kernelone.context.context_os.summarizers.contracts`:

- :class:`SummarizerGatewayProtocol` — the async compression capability the
  summarizer calls (declared in ``contracts.py``).
- :class:`SLMBinding` — the pairing of an SLM configuration object and a
  gateway, produced by the owner of the SLM runtime and installed at host
  assembly time.

The configuration object itself is **not** projected into a handful of
fields: the binding hands the summarizer the *original* configuration object
it was built with, so every non-default value (base URL, provider, model,
keep-alive, timeout, enable flag) reaches the owner unchanged.  The owner
interprets its own configuration type and its own defaults.

Dependency direction::

    roles.kernel (owner of the SLM runtime)
        ──builds──▸  SLMBinding
        ──installs──▸  slm_config.set_default_slm_binding()
    kernelone.context.context_os.summarizers
        ──consumes──▸  slm_config.get_default_slm_binding()

The KernelOne side never imports the owner Cell; it only consumes these
protocols.  Concrete gateway construction, provider wiring and model
defaults stay in the owner Cell / host wiring layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SLMConfigProtocol,
    SummarizerGatewayProtocol,
)


@runtime_checkable
class SLMBinding(Protocol):
    """Runtime binding between the SLM owner and the KernelOne summarizer.

    A binding exposes the owner's *own* configuration object plus the
    owner's gateway.  The configuration is typed by the read-only
    :class:`SLMConfigProtocol` shape so the foundation can read the SLM
    status fields without depending on the owner's concrete type.  The
    owner keeps the concrete object and may narrow/validate it inside its
    own Cell.
    """

    def config(self) -> SLMConfigProtocol:
        """Return the SLM configuration object owned by the binding.

        Returns:
            The original configuration instance (for example a
            ``TransactionConfig``) with all of its fields intact.
        """
        ...

    def gateway(self, config: SLMConfigProtocol | None = None) -> SummarizerGatewayProtocol:
        """Return the gateway used for SLM compression.

        Args:
            config: Optional configuration override supplied by the caller.
                When given, the owner builds the gateway for exactly this
                configuration, so non-default values (base URL, provider,
                model, keep-alive, timeout) are honoured.  When omitted, the
                binding's own configuration is used.

        Returns:
            The gateway implementation owned by the binding.
        """
        ...


_default_slm_binding: SLMBinding | None = None
_default_slm_binding_is_explicit: bool = False


def set_default_slm_binding(binding: SLMBinding | None, *, explicit: bool = False) -> bool:
    """Install the process-wide default SLM binding.

    Registration is idempotent:

    - a *default* installation (host assembly) never replaces an already
      installed binding, so repeated assembly passes are no-ops;
    - an *explicit* installation replaces a default binding, but never
      replaces another explicitly installed binding.

    Args:
        binding: The binding to install, or ``None`` to leave the current
            binding untouched.
        explicit: True when the caller explicitly requests this binding
            (for example a test or a host that overrides the default).

    Returns:
        True when *binding* became the installed binding, False when an
        existing binding was kept.
    """
    global _default_slm_binding, _default_slm_binding_is_explicit
    if binding is None:
        return False
    if _default_slm_binding is not None and not (explicit and not _default_slm_binding_is_explicit):
        return False
    _default_slm_binding = binding
    _default_slm_binding_is_explicit = explicit
    return True


def get_default_slm_binding() -> SLMBinding | None:
    """Return the installed default SLM binding.

    Returns:
        The installed binding, or ``None`` when the host has not installed
        one (e.g. a bare unit test that injects a gateway directly).
    """
    return _default_slm_binding


def reset_default_slm_binding() -> None:
    """Clear the installed default SLM binding.

    Intended for test isolation and for explicit host teardown.
    """
    global _default_slm_binding, _default_slm_binding_is_explicit
    _default_slm_binding = None
    _default_slm_binding_is_explicit = False


__all__ = [
    "SLMBinding",
    "SummarizerGatewayProtocol",
    "get_default_slm_binding",
    "reset_default_slm_binding",
    "set_default_slm_binding",
]
