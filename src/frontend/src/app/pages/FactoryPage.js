import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Factory 工作区页面组件
 *
 * 展示工厂模式工作区，集成 PM 和 Director
 */
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { FactoryWorkspace } from '@/app/components/factory/FactoryWorkspace';
import { BenchPanel } from '@/app/components/factory/BenchPanel';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
/**
 * Factory 工作区页面
 */
export function FactoryPage({ workspace, onBackToMain, tasks, pmTasks, directorTasks, executionLogs, llmStreamEvents, processStreamEvents, fileEditEvents, currentRun, events, artifacts, summaryMd, summaryJson, artifactsError, isArtifactsLoading, onStart, onCancel, onPause, onResume, onRetryCheckpoint, isLoading, bench, internalBenchEnabled = false, onBenchWorkspaceChange, websocketLive, websocketReconnecting, websocketAttemptCount, pmRunning, directorRunning, llmRuntimeState, currentPhase, qualityGate, controlPlaneProjection, notifyError, }) {
    return (_jsxs(ErrorBoundaryClass, { onError: (error) => notifyError(error.message || '发生未知错误'), children: [internalBenchEnabled ? (_jsx(BenchStatusStrip, { enabled: internalBenchEnabled, bench: bench, websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount })) : null, _jsx(FactoryWorkspace, { workspace: workspace, onBackToMain: onBackToMain, tasks: tasks, pmTasks: pmTasks, directorTasks: directorTasks, executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, fileEditEvents: fileEditEvents, currentRun: currentRun, events: events, artifacts: artifacts, summaryMd: summaryMd, summaryJson: summaryJson, artifactsError: artifactsError, isArtifactsLoading: isArtifactsLoading, onStart: onStart, onCancel: onCancel, onPause: onPause, onResume: onResume, onRetryCheckpoint: onRetryCheckpoint, isLoading: isLoading, bench: bench, internalBenchEnabled: internalBenchEnabled, controlPlaneProjection: controlPlaneProjection }), internalBenchEnabled ? (_jsx(BenchPanel, { enabled: internalBenchEnabled, className: "border-t border-white/10", onWorkspaceChange: onBenchWorkspaceChange })) : null, _jsx(LlmRuntimeOverlay, { activeView: "factory", websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount, pmRunning: pmRunning, directorRunning: directorRunning, llmState: llmRuntimeState.state, llmBlockedRoles: llmRuntimeState.blockedRoles, llmRequiredRoles: llmRuntimeState.requiredRoles, llmLastUpdated: llmRuntimeState.lastUpdated, currentPhase: currentPhase ?? '', qualityGate: qualityGate, executionLogs: executionLogs ?? [], llmStreamEvents: llmStreamEvents ?? [], processStreamEvents: processStreamEvents ?? [], controlPlaneProjection: controlPlaneProjection }), _jsx(Toaster, { position: "bottom-right" })] }));
}
