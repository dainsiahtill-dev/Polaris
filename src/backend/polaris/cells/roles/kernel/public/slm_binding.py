"""Public SLM binding producer for roles.kernel.

roles.kernel owns the SLM runtime: the gateway (``CognitiveGateway``), the
coprocessor (``SLMCoprocessor``) and the configuration object
(``TransactionConfig``).  This module publishes the *producer* that turns
those owned objects into the narrow KernelOne SLM port
(``polaris.kernelone.context.context_os.summarizers.slm_config``), so the
KernelOne foundation never has to import a business Cell.

Design constraints honoured here:

- No second gateway singleton: the producer reuses the warmed
  ``CognitiveGateway`` singleton when one exists and is a genuine
  ``CognitiveGateway``; otherwise it constructs a gateway exactly like the
  previous in-KernelOne implementation did.
- No duplicated defaults: the configuration always comes from
  ``TransactionConfig`` (the owner's own type).  When the caller supplies an
  explicit configuration it is used verbatim, so non-default values such as
  ``slm_base_url``, ``slm_provider``, ``slm_model_name``,
  ``slm_keep_alive``, ``slm_timeout`` and ``slm_enabled`` are honoured.
- Lazy: building the binding does not connect to the model.  The gateway is
  created on first use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
    CognitiveGateway,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import (
    SLMCoprocessor,
)
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SLMConfigProtocol,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RoleKernelSLMBinding",
    "build_slm_binding",
]


@dataclass(frozen=True)
class RoleKernelSLMBinding:
    """roles.kernel implementation of the KernelOne SLM binding port."""

    owner_config: TransactionConfig

    def config(self) -> TransactionConfig:
        """Return the owner's SLM configuration object unchanged."""
        return self.owner_config

    def gateway(self, config: SLMConfigProtocol | None = None) -> CognitiveGateway:
        """Return the SLM gateway for the requested configuration.

        The gateway that is returned always operates on the *requested*
        configuration.  A warmed ``CognitiveGateway`` singleton is only
        reused when it was built for a configuration that matches the
        request in every SLM-relevant field; otherwise a gateway is built
        for the requested configuration, so an explicit model, base URL,
        provider, keep-alive or timeout is never silently replaced by the
        warmed instance's values.

        Args:
            config: Optional configuration override.  When given it is used
                verbatim (it must be a ``TransactionConfig``); otherwise the
                binding's own configuration is used.

        Returns:
            The warmed ``CognitiveGateway`` singleton when it matches the
            requested configuration, otherwise a gateway built for exactly
            that configuration.
        """
        effective = self._resolve_config(config)
        requested_fields = _slm_config_fields(effective)

        # 优先复用全局已预热单例，避免重复创建 SLM 连接和冷启动；
        # 但只有当单例的配置与请求一致时才复用，否则显式参数会被静默丢弃。
        singleton = CognitiveGateway.get_default_instance_sync()
        # 在测试 mock 环境中，singleton 可能是 MagicMock 对象；
        # 通过检查类名来过滤，确保只复用真实的 CognitiveGateway 实例。
        if (
            singleton is not None
            and getattr(singleton, "__class__", None) is not None
            and singleton.__class__.__name__ == "CognitiveGateway"
        ):
            singleton_fields = _slm_config_fields(getattr(singleton, "config", None))
            if singleton_fields is not None and singleton_fields == requested_fields:
                logger.debug("SLM binding attached to warmed CognitiveGateway singleton")
                return singleton
            logger.debug(
                "Warmed CognitiveGateway singleton does not match the requested SLM "
                "configuration; building a gateway for the requested configuration instead"
            )

        # Fallback: 本地新建（无预热，首次调用会触发冷启动）
        slm = SLMCoprocessor(config=effective)
        return CognitiveGateway(
            config=effective,
            slm_coprocessor=slm,
        )

    def _resolve_config(self, config: SLMConfigProtocol | None) -> TransactionConfig:
        """Validate and return the effective configuration.

        Args:
            config: The requested configuration, or ``None`` to use the
                binding's own configuration.

        Returns:
            The configuration object to build the gateway with, unchanged.

        Raises:
            TypeError: When the requested configuration is not a
                ``TransactionConfig``.  The owner validates its own type
                inside its own Cell.
        """
        effective = self.owner_config if config is None else config
        if not isinstance(effective, TransactionConfig):
            raise TypeError(f"roles.kernel SLM binding requires a TransactionConfig, got {type(effective).__name__}")
        return effective


def _slm_config_fields(config: object) -> dict[str, object] | None:
    """Extract the SLM-relevant fields of a configuration object.

    Args:
        config: The configuration object to inspect, or ``None``.

    Returns:
        A comparable mapping of the SLM-relevant fields, or ``None`` when
        *config* does not expose them (so it can never be considered a
        match for a requested configuration).
    """
    if config is None:
        return None
    fields: dict[str, object] = {}
    for name in (
        "slm_enabled",
        "slm_provider",
        "slm_model_name",
        "slm_base_url",
        "slm_timeout",
        "slm_keep_alive",
    ):
        if not hasattr(config, name):
            return None
        fields[name] = getattr(config, name)
    return fields


def build_slm_binding(config: TransactionConfig | None = None) -> RoleKernelSLMBinding:
    """Build the roles.kernel SLM binding for the KernelOne summarizer port.

    The binding is lazy: no gateway and no model connection is created here.
    The gateway is produced by :meth:`RoleKernelSLMBinding.gateway` on first
    use.

    Args:
        config: Optional owner configuration.  When omitted the owner's own
            default configuration (``TransactionConfig()``) is used, so the
            model/provider/timeout defaults keep exactly one source.

    Returns:
        A binding satisfying the KernelOne ``SLMBinding`` port.
    """
    return RoleKernelSLMBinding(owner_config=config if config is not None else TransactionConfig())
