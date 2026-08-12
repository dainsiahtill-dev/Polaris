"""Lease and stage-transition lifecycle for the task-market service facade.

``LifecycleMixin`` owns the hot path: publish / claim / renew / acknowledge /
fail / requeue / dead-letter / status-query, plus the claim-candidate selector
and the terminal-dependency cascade sweep. Bodies are moved verbatim from the
original ``service.py`` so transactional behaviour is preserved exactly.

This package is the lossless successor of the former ``_service_lifecycle``
module. It re-exports every previously-public symbol from the same import path
so that ``import ...internal._service_lifecycle`` and
``from ...internal._service_lifecycle import X`` keep resolving identically for
all external importers and characterization tests.
"""

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing / sibling names that were
# module-level attributes of the former ``_service_lifecycle`` module.
import logging
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    OWNER_REWORK_HANDOFFS_METADATA_KEY,
    OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
    OWNER_REWORK_ROUTE_SCHEMA_V1,
    TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY,
    TASK_REQUEUE_RECEIPTS_METADATA_KEY,
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    OwnerReworkHandoffV1,
    OwnerReworkRouteReasonV1,
    OwnerReworkRouteResultV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
    QueryTaskRequeueReceiptV1,
    RenewTaskLeaseCommandV1,
    RequeueTaskCommandV1,
    RouteOwnerReworkCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketError,
    TaskMarketStatusResultV1,
    TaskRequeueReceiptV1,
    TaskWorkItemResultV1,
)

from .._service_base import ServiceBaseMixin
from ..claim_readiness import design_claim_ready, exec_claim_ready
from ..dlq import DLQManager
from ..errors import (
    StaleLeaseTokenError,
    StaleWriteConflictError,
    TaskMarketError as InternalTaskMarketError,
    TaskNotClaimableError,
)
from ..fsm import PRIORITY_WEIGHT
from ..lease_manager import LeaseManager
from ..models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_epoch,
    now_iso,
)
from ._constants import (
    _DEPENDENCY_TERMINAL_FAILURE_STATUSES,
    _IN_PROGRESS_STATUSES,
    _NON_CONSUMING_REQUEUE_ERROR_CODES,
)
from ._mixin import LifecycleMixin

logger = logging.getLogger(__name__)

__all__ = [
    "_DEPENDENCY_TERMINAL_FAILURE_STATUSES",
    "_IN_PROGRESS_STATUSES",
    "_NON_CONSUMING_REQUEUE_ERROR_CODES",
    "LifecycleMixin",
]
