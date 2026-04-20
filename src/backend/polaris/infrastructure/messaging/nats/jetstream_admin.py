r"""JetStream administration for Polaris runtime events.

This module provides Stream and Consumer management for JetStream,
including idempotent creation, subscription handling, and lifecycle management.

CRITICAL: All text I/O must use UTF-8 encoding explicitly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    from nats.js.errors import NotFoundError as JSConsumerNotFoundError
except ImportError as e:
    raise ImportError("nats-py is required. Install with: pip install nats-py") from e


from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from nats.js.client import JetStreamContext
    from polaris.infrastructure.messaging.nats.client import NATSClient, NATSConfig

logger = logging.getLogger(__name__)


# =============================================================================
# JetStream Admin Configuration
# =============================================================================


@dataclass
class JetStreamAdminConfig:
    r"""Configuration for JetStream administration.

    Attributes:
        stream_name: Name of the runtime stream.
        stream_description: Description for the stream.
        stream_subjects: List of subjects for the stream.
        stream_replicas: Number of stream replicas.
        stream_max_bytes: Maximum stream size in bytes.
        stream_max_age_seconds: Maximum message age in seconds.
        stream_max_msg_size: Maximum message size in bytes.
        stream_retention: Retention policy (limits, workqueue, interest).
        stream_storage: Storage type (file, memory).
        stream_discard: Discard policy when limits reached (old, new).
    """

    stream_name: str = JetStreamConstants.STREAM_NAME
    stream_description: str = JetStreamConstants.STREAM_DESCRIPTION
    stream_subjects: list[str] = field(default_factory=lambda: JetStreamConstants.STREAM_SUBJECTS)
    stream_replicas: int = JetStreamConstants.STREAM_REPLICAS
    stream_max_bytes: int = JetStreamConstants.STREAM_MAX_BYTES
    stream_max_age_seconds: int = JetStreamConstants.STREAM_MAX_AGE_SECONDS
    stream_max_msg_size: int = JetStreamConstants.STREAM_MAX_MSG_SIZE
    stream_retention: str = "limits"
    stream_storage: str = "file"
    stream_discard: str = "old"


# =============================================================================
# Stream Management
# =============================================================================


class StreamManager:
    r"""JetStream Stream management with idempotent creation.

    Provides Stream CRUD operations with idempotent creation logic,
    ensuring safe repeated initialization.

    Example:
        >>> admin = JetStreamAdmin(nats_client)
        >>> await admin.ensure_stream_exists()
    """

    def __init__(self, js: JetStreamContext, config: JetStreamAdminConfig | None = None) -> None:
        r"""Initialize Stream manager.

        Args:
            js: JetStream context.
            config: Stream configuration.
        """
        self._js = js
        self._config = config or JetStreamAdminConfig()

    def _create_stream_config(self) -> Any:
        r"""Create Stream configuration.

        Returns:
            Configured StreamConfig.
        """
        # Import nats-py types dynamically to avoid attr-defined errors
        # when the library stubs are incomplete
        try:
            from nats.js.api import (  # type: ignore[attr-defined]
                RetentionPolicy,
                StorageType,
                StreamConfig,
            )
        except ImportError:
            # Fallback: return a dict-like object if types unavailable
            return _StreamConfigFallback(self._config)

        storage_type = (
            StorageType.File  # type: ignore[attr-defined]
            if self._config.stream_storage == "file"
            else StorageType.Memory  # type: ignore[attr-defined]
        )

        retention_policy = {
            "limits": RetentionPolicy.Limits,  # type: ignore[attr-defined]
            "workqueue": RetentionPolicy.Workqueue,  # type: ignore[attr-defined]
            "interest": RetentionPolicy.Interest,  # type: ignore[attr-defined]
        }.get(self._config.stream_retention, RetentionPolicy.Limits)  # type: ignore[attr-defined]

        discard_policy = (
            StreamConfig.DISCARD_OLD  # type: ignore[attr-defined]
            if self._config.stream_discard == "old"
            else StreamConfig.DISCARD_NEW  # type: ignore[attr-defined]
        )

        return StreamConfig(
            name=self._config.stream_name,
            description=self._config.stream_description,
            subjects=self._config.stream_subjects,
            retention=retention_policy,
            storage=storage_type,
            num_replicas=self._config.stream_replicas,
            max_bytes=self._config.stream_max_bytes,
            max_age=self._config.stream_max_age_seconds * 1_000_000_000,  # Convert to nanoseconds
            max_msg_size=self._config.stream_max_msg_size,
            discard=discard_policy,
        )

    async def ensure_stream_exists(self) -> bool:
        r"""Ensure stream exists (idempotent creation).

        Returns:
            True if stream was created or already exists.

        Raises:
            RuntimeError: If stream creation fails.
        """
        try:
            # Try to get existing stream
            await self._js.stream_info(self._config.stream_name)  # type: ignore[union-attr]
            logger.info(f"Stream already exists: {self._config.stream_name}")
            return True

        except JSConsumerNotFoundError:
            # Stream doesn't exist, create it
            logger.info(f"Creating stream: {self._config.stream_name}")

            try:
                config = self._create_stream_config()
                await self._js.add_stream(config)  # type: ignore[union-attr]
                # Access config.name safely using getattr
                stream_name = getattr(config, "name", self._config.stream_name)
                logger.info(f"Stream created: {stream_name}")
                return True

            except (RuntimeError, ValueError) as e:
                logger.error(f"Failed to create stream: {e}")
                raise RuntimeError(f"Stream creation failed: {e}") from e

    async def get_stream_info(self) -> dict[str, Any] | None:
        r"""Get stream information.

        Returns:
            Stream info dictionary or None if stream doesn't exist.
        """
        try:
            info = await self._js.stream_info(self._config.stream_name)  # type: ignore[union-attr]
            # Convert to dict if needed
            if hasattr(info, "dict"):
                return info.dict()  # type: ignore[union-attr]
            return info  # type: ignore[return-value]
        except JSConsumerNotFoundError:
            return None

    async def delete_stream(self) -> bool:
        r"""Delete the stream.

        Returns:
            True if stream was deleted.
        """
        try:
            await self._js.delete_stream(self._config.stream_name)  # type: ignore[union-attr]
            logger.info(f"Stream deleted: {self._config.stream_name}")
            return True
        except JSConsumerNotFoundError:
            logger.warning(f"Stream not found for deletion: {self._config.stream_name}")
            return False
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to delete stream: {e}")
            raise

    async def update_stream(
        self,
        max_bytes: int | None = None,
        max_age_seconds: int | None = None,
    ) -> bool:
        r"""Update stream configuration.

        Args:
            max_bytes: New max bytes (optional).
            max_age_seconds: New max age in seconds (optional).

        Returns:
            True if update succeeded.
        """
        try:
            current = await self._js.stream_info(self._config.stream_name)  # type: ignore[union-attr]
            # Get config from stream info safely
            config = getattr(current, "config", None)
            if config is None:
                logger.error(f"Stream info has no config: {self._config.stream_name}")
                return False

            if max_bytes is not None:
                config.max_bytes = max_bytes  # type: ignore[union-attr]
            if max_age_seconds is not None:
                config.max_age = max_age_seconds * 1000000000  # type: ignore[union-attr]

            await self._js.update_stream(config)  # type: ignore[union-attr]
            logger.info(f"Stream updated: {self._config.stream_name}")
            return True

        except JSConsumerNotFoundError:
            logger.error(f"Stream not found for update: {self._config.stream_name}")
            return False
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to update stream: {e}")
            raise


class _StreamConfigFallback:
    """Fallback stream config when nats-py types unavailable."""

    DISCARD_OLD = "old"
    DISCARD_NEW = "new"

    def __init__(self, admin_config: JetStreamAdminConfig) -> None:
        self.name = admin_config.stream_name
        self.description = admin_config.stream_description
        self.subjects = admin_config.stream_subjects
        self.num_replicas = admin_config.stream_replicas
        self.max_bytes = admin_config.stream_max_bytes
        self.max_age = admin_config.stream_max_age_seconds * 1_000_000_000
        self.max_msg_size = admin_config.stream_max_msg_size


# =============================================================================
# Consumer Management
# =============================================================================


class ConsumerManager:
    r"""JetStream Consumer management with durable consumers.

    Provides Consumer CRUD operations with durable consumer support
    for reliable message delivery.

    Example:
        >>> consumer_mgr = ConsumerManager(nats_client)
        >>> await consumer_mgr.create_consumer("my_consumer", "hp.runtime.>")
    """

    def __init__(self, js: JetStreamContext, stream_name: str) -> None:
        r"""Initialize Consumer manager.

        Args:
            js: JetStream context.
            stream_name: Name of the stream to consume from.
        """
        self._js = js
        self._stream_name = stream_name

    def _create_consumer_config(
        self,
        consumer_name: str,
        subject: str,
        delivery_subject: str | None = None,
        durable: bool = True,
        deliver_policy: str = "all",
        ack_policy: str = "explicit",
        ack_wait_seconds: int = JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
        max_deliver: int = JetStreamConstants.CONSUMER_MAX_DELIVER,
        max_ack_pending: int = JetStreamConstants.CONSUMER_MAX_ACK_PENDING,
    ) -> Any:
        r"""Create Consumer configuration.

        Args:
            consumer_name: Name of the consumer.
            subject: Subject to subscribe to.
            delivery_subject: Push delivery subject (optional).
            durable: Whether consumer is durable.
            deliver_policy: Delivery policy (all, last, new, by_start_sequence, by_start_time).
            ack_policy: Acknowledge policy (none, all, explicit).
            ack_wait_seconds: Acknowledge wait time.
            max_deliver: Maximum delivery attempts.
            max_ack_pending: Maximum pending acknowledges.

        Returns:
            Configured ConsumerConfig.
        """
        # Import nats-py types dynamically to avoid attr-defined errors
        try:
            from nats.js.api import (  # type: ignore[attr-defined]
                AckPolicy,
                ConsumerConfig,
                DeliverPolicy,
                ReplayPolicy,
            )
        except ImportError:
            # Fallback: return a dict-like object if types unavailable
            return _ConsumerConfigFallback(
                consumer_name=consumer_name,
                subject=subject,
                delivery_subject=delivery_subject,
                durable=durable,
                ack_wait_seconds=ack_wait_seconds,
                max_deliver=max_deliver,
                max_ack_pending=max_ack_pending,
            )

        deliver_policy_map = {
            "all": DeliverPolicy.ALL,  # type: ignore[attr-defined]
            "last": DeliverPolicy.LAST,  # type: ignore[attr-defined]
            "new": DeliverPolicy.NEW,  # type: ignore[attr-defined]
            "by_start_sequence": DeliverPolicy.BY_START_SEQUENCE,  # type: ignore[attr-defined]
            "by_start_time": DeliverPolicy.BY_START_TIME,  # type: ignore[attr-defined]
        }

        ack_policy_map = {
            "none": AckPolicy.NONE,  # type: ignore[attr-defined]
            "all": AckPolicy.ALL,  # type: ignore[attr-defined]
            "explicit": AckPolicy.EXPLICIT,  # type: ignore[attr-defined]
        }

        return ConsumerConfig(
            durable_name=consumer_name if durable else None,
            deliver_subject=delivery_subject,
            deliver_policy=deliver_policy_map.get(deliver_policy, DeliverPolicy.ALL),  # type: ignore[attr-defined]
            ack_policy=ack_policy_map.get(ack_policy, AckPolicy.EXPLICIT),  # type: ignore[attr-defined]
            ack_wait=ack_wait_seconds * 1_000_000_000,
            max_deliver=max_deliver,
            max_ack_pending=max_ack_pending,
            filter_subject=subject,
            replay_policy=ReplayPolicy.INSTANT,  # type: ignore[attr-defined]
        )

    async def create_consumer(
        self,
        consumer_name: str,
        subject: str,
        delivery_subject: str | None = None,
        durable: bool = True,
    ) -> bool:
        r"""Create consumer (idempotent).

        Args:
            consumer_name: Name of the consumer.
            subject: Subject to subscribe to.
            delivery_subject: Push delivery subject (optional).
            durable: Whether consumer is durable.

        Returns:
            True if consumer was created or already exists.
        """
        try:
            # Try to get existing consumer
            await self._js.consumer_info(self._stream_name, consumer_name)  # type: ignore[union-attr]
            logger.info(f"Consumer already exists: {consumer_name}")
            return True

        except JSConsumerNotFoundError:
            # Create consumer
            config = self._create_consumer_config(
                consumer_name=consumer_name,
                subject=subject,
                delivery_subject=delivery_subject,
                durable=durable,
            )

            try:
                await self._js.add_consumer(self._stream_name, config)  # type: ignore[union-attr]
                logger.info(f"Consumer created: {consumer_name}")
                return True

            except (RuntimeError, ValueError) as e:
                logger.error(f"Failed to create consumer: {e}")
                raise RuntimeError(f"Consumer creation failed: {e}") from e

    async def get_consumer_info(self, consumer_name: str) -> dict[str, Any] | None:
        r"""Get consumer information.

        Args:
            consumer_name: Name of the consumer.

        Returns:
            Consumer info dictionary or None if not found.
        """
        try:
            info = await self._js.consumer_info(self._stream_name, consumer_name)  # type: ignore[union-attr]
            # Convert to dict if needed
            if hasattr(info, "dict"):
                return info.dict()  # type: ignore[union-attr]
            return info  # type: ignore[return-value]
        except JSConsumerNotFoundError:
            return None

    async def delete_consumer(self, consumer_name: str) -> bool:
        r"""Delete consumer.

        Args:
            consumer_name: Name of the consumer.

        Returns:
            True if consumer was deleted.
        """
        try:
            await self._js.delete_consumer(self._stream_name, consumer_name)  # type: ignore[union-attr]
            logger.info(f"Consumer deleted: {consumer_name}")
            return True
        except JSConsumerNotFoundError:
            logger.warning(f"Consumer not found: {consumer_name}")
            return False
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to delete consumer: {e}")
            raise

    async def list_consumers(self) -> list[dict[str, Any]]:
        r"""List all consumers in the stream.

        Returns:
            List of consumer info dictionaries.
        """
        try:
            consumers = await self._js.consumers_info(self._stream_name)  # type: ignore[union-attr]
            result: list[dict[str, Any]] = []
            for c in consumers:
                if hasattr(c, "dict"):
                    result.append(c.dict())  # type: ignore[union-attr]
                else:
                    # Fallback: convert to dict if possible
                    result.append({"info": c})  # type: ignore[arg-type]
            return result
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to list consumers: {e}")
            return []


class _ConsumerConfigFallback:
    """Fallback consumer config when nats-py types unavailable."""

    def __init__(
        self,
        consumer_name: str,
        subject: str,
        delivery_subject: str | None,
        durable: bool,
        ack_wait_seconds: int,
        max_deliver: int,
        max_ack_pending: int,
    ) -> None:
        self.durable_name = consumer_name if durable else None
        self.deliver_subject = delivery_subject
        self.filter_subject = subject
        self.ack_wait = ack_wait_seconds * 1_000_000_000
        self.max_deliver = max_deliver
        self.max_ack_pending = max_ack_pending


# =============================================================================
# JetStream Admin
# =============================================================================


class JetStreamAdmin:
    r"""Combined JetStream administration interface.

    Provides unified Stream and Consumer management with convenient
    high-level operations.

    Example:
        >>> async with create_jetstream_admin() as admin:
        ...     await admin.ensure_stream_exists()
        ...     await admin.subscribe("my_consumer", "hp.runtime.>")
    """

    def __init__(
        self,
        nats_client: NATSClient,
        config: JetStreamAdminConfig | None = None,
    ) -> None:
        r"""Initialize JetStream admin.

        Args:
            nats_client: NATS client instance.
            config: JetStream configuration.
        """
        self._client = nats_client
        self._config = config or JetStreamAdminConfig()

        self._stream_manager: StreamManager | None = None
        self._consumer_managers: dict[str, ConsumerManager] = {}

    @property
    def stream_manager(self) -> StreamManager:
        r"""Get Stream manager.

        Returns:
            StreamManager instance.
        """
        if self._stream_manager is None:
            # Access jetstream safely using getattr
            js = getattr(self._client, "jetstream", None)
            if js is None:
                raise RuntimeError("JetStream not available")
            self._stream_manager = StreamManager(
                js,  # type: ignore[arg-type]
                self._config,
            )
        return self._stream_manager

    def get_consumer_manager(self, stream_name: str | None = None) -> ConsumerManager:
        r"""Get Consumer manager for stream.

        Args:
            stream_name: Stream name (uses config default if not provided).

        Returns:
            ConsumerManager instance.
        """
        stream = stream_name or self._config.stream_name

        if stream not in self._consumer_managers:
            # Access jetstream safely using getattr
            js = getattr(self._client, "jetstream", None)
            if js is None:
                raise RuntimeError("JetStream not available")
            self._consumer_managers[stream] = ConsumerManager(
                js,  # type: ignore[arg-type]
                stream,
            )

        return self._consumer_managers[stream]

    async def ensure_stream_exists(self) -> bool:
        r"""Ensure runtime stream exists (idempotent).

        Returns:
            True if stream was created or already exists.
        """
        return await self.stream_manager.ensure_stream_exists()

    async def ensure_consumer(
        self,
        consumer_name: str,
        subject: str,
        delivery_subject: str | None = None,
    ) -> bool:
        r"""Ensure consumer exists (idempotent).

        Args:
            consumer_name: Name of the consumer.
            subject: Subject to subscribe to.
            delivery_subject: Push delivery subject.

        Returns:
            True if consumer was created or already exists.
        """
        return await self.get_consumer_manager().create_consumer(
            consumer_name=consumer_name,
            subject=subject,
            delivery_subject=delivery_subject,
        )

    async def subscribe(
        self,
        consumer_name: str,
        subject: str,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        r"""Subscribe to subject with consumer.

        Args:
            consumer_name: Name of the consumer.
            subject: Subject to subscribe to.
            callback: Optional callback for each message.

        Yields:
            Message payloads as dictionaries.

        Example:
            >>> async for msg in admin.subscribe("my_consumer", "hp.runtime.>"):
            ...     print(msg)
        """
        # Access jetstream safely using getattr
        js = getattr(self._client, "jetstream", None)
        if not js:
            raise RuntimeError("JetStream not available")

        # Ensure consumer exists
        await self.ensure_consumer(consumer_name, subject)

        # Pull consumer messages
        async def pull_messages() -> AsyncIterator[dict[str, Any]]:
            try:
                # Use pull_subscribe with proper typing
                consumer = await js.pull_subscribe(  # type: ignore[union-attr]
                    subject,
                    durable=consumer_name,
                    stream=self._config.stream_name,
                )
                async for msg in consumer.messages():  # type: ignore[union-attr]
                    try:
                        import json

                        data = json.loads(msg.data.decode("utf-8"))  # type: ignore[union-attr]
                        if callback:
                            callback(data)
                        yield data
                        await msg.ack()  # type: ignore[union-attr]
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON: {e}")
                    except (RuntimeError, ValueError) as e:
                        logger.error(f"Message error: {e}")
            except (RuntimeError, ValueError) as e:
                logger.error(f"Subscribe error: {e}")
                raise

        async for msg in pull_messages():
            yield msg

    async def get_stream_info(self) -> dict[str, Any] | None:
        r"""Get stream information.

        Returns:
            Stream info dictionary or None.
        """
        return await self.stream_manager.get_stream_info()

    async def get_consumer_info(
        self,
        consumer_name: str,
    ) -> dict[str, Any] | None:
        r"""Get consumer information.

        Args:
            consumer_name: Name of the consumer.

        Returns:
            Consumer info dictionary or None.
        """
        return await self.get_consumer_manager().get_consumer_info(consumer_name)

    async def cleanup(self) -> None:
        r"""Clean up resources."""
        self._consumer_managers.clear()


# =============================================================================
# Factory Functions
# =============================================================================


async def create_jetstream_admin(
    config: NATSConfig | None = None,
    js_config: JetStreamAdminConfig | None = None,
) -> AsyncIterator[JetStreamAdmin]:
    r"""Create JetStream admin with connection management.

    Args:
        config: NATS configuration.
        js_config: JetStream configuration.

    Yields:
        Configured JetStreamAdmin instance.

    Example:
        >>> async with create_jetstream_admin() as admin:
        ...     await admin.ensure_stream_exists()
    """
    from polaris.infrastructure.messaging.nats.client import create_nats_client

    async with create_nats_client(config) as client:
        admin = JetStreamAdmin(client, js_config)
        yield admin


__all__ = [
    "ConsumerManager",
    "JetStreamAdmin",
    "JetStreamAdminConfig",
    "StreamManager",
    "create_jetstream_admin",
]
