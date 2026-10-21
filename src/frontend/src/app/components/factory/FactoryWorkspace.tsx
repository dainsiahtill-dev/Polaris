import { EngineeringModeBoundary } from './EngineeringRunWorkspace';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import type { FileEditEvent } from '@/app/hooks/useRuntimeStore';
import type { ControlPlaneProjection } from '@/services/controlPlane';
import type { FactoryAuditEvent, FactoryRunArtifact, FactoryRunStatus } from '@/hooks/useFactory';
import type { UseFactoryBenchResult } from '@/hooks/useFactoryBench';
import type { LogEntry } from '@/types/log';
import type { PmTask } from '@/types/task';
interface FactoryWorkspaceProps {
  engineering?: EngineeringRunBinding;
  engineeringEnabled?: boolean;
  onEngineeringModeChange?: (enabled: boolean) => void;
  onOpenEngineeringContext?: () => void;
  workspace: string;
  onBackToMain: () => void;
  tasks: PmTask[];
  pmTasks?: PmTask[];
  directorTasks?: PmTask[];
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  fileEditEvents?: FileEditEvent[];
  currentRun?: FactoryRunStatus | null;
  events?: FactoryAuditEvent[];
  artifacts?: FactoryRunArtifact[];
  summaryMd?: string | null;
  summaryJson?: Record<string, unknown> | null;
  artifactsError?: string | null;
  isArtifactsLoading?: boolean;
  onStart?: () => void;
  onCancel?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onRetryCheckpoint?: () => void;
  isLoading?: boolean;
  bench?: UseFactoryBenchResult;
  internalBenchEnabled?: boolean;
  websocketLive?: boolean;
  websocketReconnecting?: boolean;
  websocketAttemptCount?: number;
  controlPlaneProjection?: ControlPlaneProjection | null;
}

/** Compatibility entry: new work always uses engineering; history has its own read-only viewer. */
export function FactoryWorkspace(props: FactoryWorkspaceProps) {
  return <EngineeringModeBoundary binding={props.engineering} workspace={props.workspace} role="director" onBackToMain={props.onBackToMain} onOpenContext={props.onOpenEngineeringContext} />;
}
