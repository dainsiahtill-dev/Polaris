import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Director 工作区页面组件
 *
 * 展示 Director 任务执行界面
 */
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { DirectorWorkspace } from '@/app/components/director';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
import { getRoleLlmBlockedReason } from '@/app/hooks/useLlmRuntimeGate';
function resolveDirectorStartBlockedReason({ directorRunning, agentsRequired, agentsDraftReady, agentsDraftFailed, llmRuntimeState, }) {
    if (directorRunning) {
        return '';
    }
    if (agentsRequired) {
        if (agentsDraftFailed) {
            return 'AGENTS 草稿生成失败，请返回主界面重新生成或人工处理后再启动 Director。';
        }
        return agentsDraftReady
            ? '需要先确认 AGENTS.md 后才能启动 Director。'
            : 'AGENTS.md 审核未完成，等待草稿生成或人工确认后才能启动 Director。';
    }
    return getRoleLlmBlockedReason(llmRuntimeState, 'director', 'Director');
}
/**
 * Director 工作区页面
 */
export function DirectorPage({ workspace, tasks, workers, directorRunning, pmRunning = false, isStarting, isStopping, onToggleDirector, directorStartBlockedReason = '', onOpenSettings, currentTaskId, currentTaskTitle, currentTaskStatus, onBackToMain, fileEditEvents, executionLogs, llmStreamEvents, processStreamEvents, currentPhase, taskProgressMap, taskTraceMap, websocketLive, websocketReconnecting, websocketAttemptCount, internalBenchEnabled = false, llmRuntimeState, agentsRequired = false, agentsDraftReady = false, agentsDraftFailed = false, qualityGate, controlPlaneProjection, notifyError, }) {
    const localStartBlockedReason = resolveDirectorStartBlockedReason({
        directorRunning,
        agentsRequired,
        agentsDraftReady,
        agentsDraftFailed,
        llmRuntimeState,
    });
    const startBlockedReason = !directorRunning && directorStartBlockedReason.trim()
        ? directorStartBlockedReason.trim()
        : localStartBlockedReason;
    return (_jsxs(ErrorBoundaryClass, { onError: (error) => notifyError(error.message || '发生未知错误'), children: [_jsx(BenchStatusStrip, { enabled: internalBenchEnabled, websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount }), _jsx(DirectorWorkspace, { workspace: workspace, onBackToMain: onBackToMain, tasks: tasks, workers: workers, directorRunning: directorRunning, isStarting: isStarting, isStopping: isStopping, startBlockedReason: startBlockedReason, onToggleDirector: () => onToggleDirector(), onOpenSettings: onOpenSettings, currentTaskId: currentTaskId ?? null, currentTaskTitle: currentTaskTitle ?? null, currentTaskStatus: currentTaskStatus ?? null, fileEditEvents: fileEditEvents, executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase, taskProgressMap: taskProgressMap, taskTraceMap: taskTraceMap }), _jsx(LlmRuntimeOverlay, { activeView: "director", websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount, pmRunning: pmRunning, directorRunning: directorRunning, llmState: llmRuntimeState.state, llmBlockedRoles: llmRuntimeState.blockedRoles, llmRequiredRoles: llmRuntimeState.requiredRoles, llmLastUpdated: llmRuntimeState.lastUpdated, currentPhase: currentPhase ?? '', qualityGate: qualityGate, executionLogs: executionLogs ?? [], llmStreamEvents: llmStreamEvents ?? [], processStreamEvents: processStreamEvents ?? [], fileEditEvents: fileEditEvents, controlPlaneProjection: controlPlaneProjection }), _jsx(Toaster, { position: "bottom-right" })] }));
}
