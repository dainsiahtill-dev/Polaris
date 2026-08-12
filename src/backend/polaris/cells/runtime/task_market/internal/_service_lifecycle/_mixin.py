"""Composed LifecycleMixin facade."""

from __future__ import annotations

from .._service_base import ServiceBaseMixin
from ._acknowledge import AcknowledgeMixin
from ._cascade_status import CascadeStatusMixin
from ._fail_retry import FailRetryMixin
from ._helpers import HelpersMixin
from ._owner_rework import OwnerReworkMixin
from ._publish_claim import PublishClaimMixin
from ._requeue_dlq import RequeueDlqMixin


class LifecycleMixin(
    PublishClaimMixin,
    AcknowledgeMixin,
    FailRetryMixin,
    OwnerReworkMixin,
    RequeueDlqMixin,
    CascadeStatusMixin,
    HelpersMixin,
    ServiceBaseMixin,
):
    """Lease-aware publish / claim / acknowledge / fail / requeue path."""
