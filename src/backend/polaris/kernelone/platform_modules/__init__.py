"""Platform module solidification, residual attribution, unattended step plans.

Sealed modules have fixed invariants and targeted pytest suites. Changes to a
sealed module require an explicit unfreeze and must re-pass the module gate
before cascade/bench gates may claim progress. This exists to stop the
R116–R153 pattern of infinite linear defect chasing without durable module
boundaries.

Unattended automation consumers must:
1. attribute residuals to exactly one module_id (residual_attribution);
2. plan next step under five modular rules (unattended_supervisor);
3. never treat external agent reports as platform success conditions.
"""

from __future__ import annotations

from polaris.kernelone.platform_modules.formal_run_admission import (
    FormalRunAdmissionV1,
    evaluate_formal_run_admission,
)
from polaris.kernelone.platform_modules.registry import (
    MODULE_CASCADE_ORDER,
    PLATFORM_MODULES,
    PlatformModuleRecord,
    PlatformModuleStatus,
    get_module,
    list_modules,
    modules_by_status,
)
from polaris.kernelone.platform_modules.residual_attribution import (
    ResidualAttributionV1,
    attribute_factory_audit_record,
    attribute_factory_audits_file,
    attribute_residual,
    build_factory_audits_attribution_pack,
    classify_delivery_status,
)
from polaris.kernelone.platform_modules.unattended_supervisor import (
    UnattendedStepPlanV1,
    plan_from_factory_audit_record,
    plan_unattended_step,
)

__all__ = [
    "MODULE_CASCADE_ORDER",
    "PLATFORM_MODULES",
    "FormalRunAdmissionV1",
    "PlatformModuleRecord",
    "PlatformModuleStatus",
    "ResidualAttributionV1",
    "UnattendedStepPlanV1",
    "attribute_factory_audit_record",
    "attribute_factory_audits_file",
    "attribute_residual",
    "build_factory_audits_attribution_pack",
    "classify_delivery_status",
    "evaluate_formal_run_admission",
    "get_module",
    "list_modules",
    "modules_by_status",
    "plan_from_factory_audit_record",
    "plan_unattended_step",
]
