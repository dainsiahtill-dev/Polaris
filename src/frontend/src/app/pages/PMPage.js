import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * PM 工作区页面组件
 *
 * 展示 PM 任务管理界面
 */
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { PMWorkspace } from '@/app/components/pm';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
/**
 * PM 工作区页面
 */
export function PMPage({ workspace, tasks, pmState, pmRunning, directorRunning = false, pmTerminalStatus = null, pmStartBlockedReason = '', runtimeIssue = null, isStarting, isStopping = false, onTogglePm, onRunPmOnce, onBackToMain, onOpenSettings, websocketLive, websocketReconnecting, websocketAttemptCount, internalBenchEnabled = false, llmRuntimeState, currentPhase, qualityGate, executionLogs, llmStreamEvents, processStreamEvents, fileEditEvents, controlPlaneProjection, taskTraceMap, notifyError, }) {
    return (_jsxs(ErrorBoundaryClass, { onError: (error) => notifyError(error.message || '发生未知错误'), children: [_jsx(BenchStatusStrip, { enabled: internalBenchEnabled, websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount }), _jsx(PMWorkspace, { tasks: tasks, pmState: pmState, pmRunning: pmRunning, pmTerminalStatus: pmTerminalStatus, pmStartBlockedReason: pmStartBlockedReason, runtimeIssue: runtimeIssue, isStarting: isStarting, isStopping: isStopping, onBackToMain: onBackToMain, onTogglePm: onTogglePm, onRunPmOnce: onRunPmOnce, onOpenSettings: onOpenSettings, workspace: workspace, executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase, qualityGate: qualityGate, taskTraceMap: taskTraceMap, llmRuntimeState: llmRuntimeState }), _jsx(LlmRuntimeOverlay, { activeView: "pm", websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount, pmRunning: pmRunning, directorRunning: directorRunning, llmState: llmRuntimeState.state, llmBlockedRoles: llmRuntimeState.blockedRoles, llmRequiredRoles: llmRuntimeState.requiredRoles, llmLastUpdated: llmRuntimeState.lastUpdated, currentPhase: currentPhase ?? '', qualityGate: qualityGate, executionLogs: executionLogs ?? [], llmStreamEvents: llmStreamEvents ?? [], processStreamEvents: processStreamEvents ?? [], fileEditEvents: fileEditEvents, controlPlaneProjection: controlPlaneProjection }), _jsx(Toaster, { position: "bottom-right" })] }));
}
