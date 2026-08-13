/**
 * contextOSData package — thin facade re-exporting the complete surface.
 * Lossless successor of the former contextOSData.ts single file.
 */


export type {
  BudgetSlice,
  ComponentHealth,
  ContextOSModel,
  DecisionRow,
  EventTypeSlice,
  PipelineStage,
  PipelineState,
  RoleBindingBudget,
  RoleCard,
  RoleInternalContext,
  WorkerCard,
} from './_types';

export {
  ROLE_DECISION_ALIASES,
} from './_constants';

export type {
  EntityLifecycleStep,
  EntityThread,
  EntityThreadSummary,
  EventEntity,
  EventSemantics,
  GroupedEvents,
  RoleContextSummary,
} from './_model';

export {
  buildContextOSModel,
  classifyEventSemantics,
  contextOSFormat,
  decisionMatchesRole,
  groupEventsByEntity,
  safeText,
  summarizeEntityThread,
  summarizeRoleContextState,
} from './_model';

