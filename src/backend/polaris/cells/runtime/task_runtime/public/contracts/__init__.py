"""Public contracts for `runtime.task_runtime` cell.

The contracts in this package define the stable boundary for runtime-task
lifecycle, execution attempts, and directed-effect inventory/parent/operation
payloads.

This package is the lossless successor of the former ``contracts`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...public.contracts`` and ``from ...public.contracts import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_common import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1 as _DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1 as _DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1 as _DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1 as _DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V1 as _DIRECTED_EFFECT_OPERATION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V2 as _DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V3 as _DIRECTED_EFFECT_OPERATION_SCHEMA_V3,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V4 as _DIRECTED_EFFECT_OPERATION_SCHEMA_V4,
    DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1 as _DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1 as _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2 as _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3 as _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3,
    DirectedEffectAuthorityFailureCodeV1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryContingencyKindV1,
    DirectedEffectInventoryEffectTypeV1,
    DirectedEffectInventoryExecutionModeV1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentReadinessCodeV1,
    DirectedEffectReceiptOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_inventory import (
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._directed_effect_operations import (
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    AdmitDirectedEffectParentCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectClaimGrantV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectOperationResultV1,
    DirectedEffectOperationSnapshotV1,
    DirectedEffectParentReadinessProjectionV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentReadinessStateCountV1,
    DirectedEffectParentRegistryProjectionV1,
    DirectedEffectParentRegistryResultV1,
    DirectedEffectRecoverySweepItemV1,
    DirectedEffectRecoverySweepResultV1,
    DirectedEffectStreamEnrollmentCodeV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ReconcileAmbiguousDirectedEffectsCommandV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._execution_attempts import (
    TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1 as _TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    RuntimeTaskRuntimeError,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptAuthorityHeartbeatCodeV1,
    TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptAuthorityOpenCodeV1,
    TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySettlementCodeV1,
    TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySnapshotCodeV1,
    TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
    TaskRuntimeExecutionAttemptHeartbeatCodeV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementCodeV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)
from polaris.cells.runtime.task_runtime.public.contracts._lifecycle import (
    OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
    SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
    TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
    TASK_RUNTIME_EXECUTION_SOURCE_V1 as _TASK_RUNTIME_EXECUTION_SOURCE_V1,
    TASK_RUNTIME_EXECUTION_STREAM_V1 as _TASK_RUNTIME_EXECUTION_STREAM_V1,
    BindRuntimeTaskToFactoryRunCommandV1,
    CreateRuntimeTaskCommandV1,
    ExpiredFactoryRunSessionFenceCodeV1,
    ExpiredFactoryRunSessionFenceResultV1,
    FenceExpiredFactoryRunSessionsCommandV1,
    GetRuntimeTaskQueryV1,
    ListRuntimeTasksQueryV1,
    ObservableTaskRowsProjectionV1,
    OwnerReworkExecutionAuthorizationV1,
    OwnerReworkExecutionPreparationCodeV1,
    OwnerReworkExecutionPreparationResultV1,
    PrepareOwnerReworkExecutionCommandV1,
    PrepareSameTaskLocalReworkCommandV1,
    QuerySameTaskLocalReworkAuthorizationV1,
    ReopenRuntimeTaskCommandV1,
    RuntimeTaskFactoryRunBindingCodeV1,
    RuntimeTaskFactoryRunBindingResultV1,
    RuntimeTaskLifecycleEventV1,
    RuntimeTaskResultV1,
    SameTaskLocalReworkAuthorizationQueryCodeV1,
    SameTaskLocalReworkAuthorizationQueryResultV1,
    SameTaskLocalReworkPreparationCodeV1,
    SameTaskLocalReworkPreparationResultV1,
    TaskRuntimeExecutionFactV1,
    UpdateRuntimeTaskCommandV1,
)

# Rebind Final constants on the package surface so get_type_hints remains lossless
# after the module->package split (import aliases alone do not carry annotations).
TASK_RUNTIME_EXECUTION_STREAM_V1: Final[str] = _TASK_RUNTIME_EXECUTION_STREAM_V1
TASK_RUNTIME_EXECUTION_SOURCE_V1: Final[str] = _TASK_RUNTIME_EXECUTION_SOURCE_V1
TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1: Final[str] = _TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1
DIRECTED_EFFECT_OPERATION_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_OPERATION_SCHEMA_V1
DIRECTED_EFFECT_OPERATION_SCHEMA_V2: Final[str] = _DIRECTED_EFFECT_OPERATION_SCHEMA_V2
DIRECTED_EFFECT_OPERATION_SCHEMA_V3: Final[str] = _DIRECTED_EFFECT_OPERATION_SCHEMA_V3
DIRECTED_EFFECT_OPERATION_SCHEMA_V4: Final[str] = _DIRECTED_EFFECT_OPERATION_SCHEMA_V4
DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1
DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1
DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1
DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1
DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1
DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1
DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1
DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2: Final[str] = _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3: Final[str] = _DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3
DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1: Final[str] = _DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1
DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1: Final[str] = (
    _DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1
)

if TYPE_CHECKING:
    from ..service import TaskRuntimeExecutionAttemptAuthorityV1

__all__ = [
    "DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V1",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V2",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V3",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V4",
    "DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3",
    "OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1",
    "SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_SOURCE_V1",
    "TASK_RUNTIME_EXECUTION_STREAM_V1",
    "AbortDirectedEffectOperationCommandV1",
    "AdmitDirectedEffectOperationCommandV1",
    "AdmitDirectedEffectParentBatchCommandV1",
    "AdmitDirectedEffectParentCommandV1",
    "BindRuntimeTaskToFactoryRunCommandV1",
    "ClaimDirectedEffectCommandV1",
    "CommitDirectedEffectReceiptCommandV1",
    "CreateRuntimeTaskCommandV1",
    "DeadLetterDirectedEffectOperationCommandV1",
    "DirectedEffectAuthorityFailureCodeV1",
    "DirectedEffectClaimGrantV1",
    "DirectedEffectInventoryCodeV1",
    "DirectedEffectInventoryContingencyKindV1",
    "DirectedEffectInventoryEffectTypeV1",
    "DirectedEffectInventoryExecutionModeV1",
    "DirectedEffectInventoryIntentV1",
    "DirectedEffectInventoryMemberV1",
    "DirectedEffectInventoryProjectionV1",
    "DirectedEffectInventoryResultV1",
    "DirectedEffectOperationCodeV1",
    "DirectedEffectOperationIdentityV1",
    "DirectedEffectOperationResultV1",
    "DirectedEffectOperationSnapshotV1",
    "DirectedEffectOperationStateV1",
    "DirectedEffectParentBindingV1",
    "DirectedEffectParentReadinessCodeV1",
    "DirectedEffectParentReadinessProjectionV1",
    "DirectedEffectParentReadinessResultV1",
    "DirectedEffectParentReadinessStateCountV1",
    "DirectedEffectParentRegistryIdentityV1",
    "DirectedEffectParentRegistryProjectionV1",
    "DirectedEffectParentRegistryResultV1",
    "DirectedEffectReceiptOutcomeV1",
    "DirectedEffectStreamEnrollmentCodeV1",
    "DirectedEffectStreamEnrollmentResultV1",
    "EnrollDirectedEffectOperationStreamCommandV1",
    "EnrollDirectedEffectParentRegistryStreamCommandV1",
    "ExpiredFactoryRunSessionFenceCodeV1",
    "ExpiredFactoryRunSessionFenceResultV1",
    "FenceExpiredFactoryRunSessionsCommandV1",
    "FinalizeDirectedEffectInventoryAdmissionCommandV1",
    "GetDirectedEffectInventoryQueryV1",
    "GetDirectedEffectOperationQueryV1",
    "GetDirectedEffectParentReadinessQueryV1",
    "GetDirectedEffectParentRegistryQueryV1",
    "GetRuntimeTaskQueryV1",
    "HeartbeatTaskRuntimeExecutionAttemptCommandV1",
    "ListRuntimeTasksQueryV1",
    "MarkDirectedEffectRecoveryPendingCommandV1",
    "ObservableTaskRowsProjectionV1",
    "OpenTaskRuntimeExecutionAttemptAuthorityCommandV1",
    "OwnerReworkExecutionAuthorizationV1",
    "OwnerReworkExecutionPreparationCodeV1",
    "OwnerReworkExecutionPreparationResultV1",
    "ParentCorrelationV1",
    "PrepareOwnerReworkExecutionCommandV1",
    "PrepareSameTaskLocalReworkCommandV1",
    "QuerySameTaskLocalReworkAuthorizationV1",
    "ReopenRuntimeTaskCommandV1",
    "RuntimeTaskFactoryRunBindingCodeV1",
    "RuntimeTaskFactoryRunBindingResultV1",
    "RuntimeTaskLifecycleEventV1",
    "RuntimeTaskResultV1",
    "RuntimeTaskRuntimeError",
    "SameTaskLocalReworkAuthorizationQueryCodeV1",
    "SameTaskLocalReworkAuthorizationQueryResultV1",
    "SameTaskLocalReworkPreparationCodeV1",
    "SameTaskLocalReworkPreparationResultV1",
    "SealDirectedEffectInventoryCommandV1",
    "SettleTaskRuntimeExecutionAttemptCommandV1",
    "TaskRuntimeExecutionAttemptAuthorityHeartbeatCodeV1",
    "TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1",
    "TaskRuntimeExecutionAttemptAuthorityOpenCodeV1",
    "TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1",
    "TaskRuntimeExecutionAttemptAuthoritySettlementCodeV1",
    "TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1",
    "TaskRuntimeExecutionAttemptAuthoritySnapshotCodeV1",
    "TaskRuntimeExecutionAttemptAuthoritySnapshotV1",
    "TaskRuntimeExecutionAttemptHeartbeatCodeV1",
    "TaskRuntimeExecutionAttemptHeartbeatVerdictV1",
    "TaskRuntimeExecutionAttemptIdentityV1",
    "TaskRuntimeExecutionAttemptSettlementCodeV1",
    "TaskRuntimeExecutionAttemptSettlementOutcomeV1",
    "TaskRuntimeExecutionAttemptSettlementVerdictV1",
    "TaskRuntimeExecutionAttemptValidationCodeV1",
    "TaskRuntimeExecutionAttemptValidationVerdictV1",
    "TaskRuntimeExecutionFactV1",
    "UpdateRuntimeTaskCommandV1",
    "ValidateTaskRuntimeExecutionAttemptQueryV1",
]
