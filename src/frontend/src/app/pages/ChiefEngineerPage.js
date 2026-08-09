import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Chief Engineer 工作区页面组件
 *
 * 展示 Chief Engineer 蓝图与执行前置条件控制界面。
 */
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { ChiefEngineerWorkspace } from '@/app/components/chief-engineer';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
import { getRoleLlmBlockedReason } from '@/app/hooks/useLlmRuntimeGate';
function resolveChiefEngineerDirectorStartBlockedReason({ directorRunning, agentsRequired, agentsDraftReady, agentsDraftFailed, llmRuntimeState, }) {
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
 * Chief Engineer 工作区页面
 */
export function ChiefEngineerPage({ workspace, engineStatus, tasks, workers, pmState, pmRunning = false, directorRunning, isStartingDirector, isStoppingDirector = false, agentsRequired = false, agentsDraftReady = false, agentsDraftFailed = false, onBackToMain, onEnterDirectorWorkspace, onOpenSettings, onToggleDirector, directorStartBlockedReason: externalDirectorStartBlockedReason = '', websocketLive, websocketReconnecting, websocketAttemptCount, internalBenchEnabled = false, llmRuntimeState, currentPhase, qualityGate, executionLogs, llmStreamEvents, processStreamEvents, fileEditEvents, controlPlaneProjection, notifyError, }) {
    const localDirectorStartBlockedReason = resolveChiefEngineerDirectorStartBlockedReason({
        directorRunning,
        agentsRequired,
        agentsDraftReady,
        agentsDraftFailed,
        llmRuntimeState,
    });
    const directorStartBlockedReason = !directorRunning && externalDirectorStartBlockedReason.trim()
        ? externalDirectorStartBlockedReason.trim()
        : localDirectorStartBlockedReason;
    return (_jsxs(ErrorBoundaryClass, { onError: (error) => notifyError(error.message || '发生未知错误'), children: [_jsx(BenchStatusStrip, { enabled: internalBenchEnabled, websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount }), _jsx(ChiefEngineerWorkspace, { workspace: workspace, engineStatus: engineStatus, tasks: tasks, workers: workers ?? [], pmState: pmState, directorRunning: directorRunning, isStartingDirector: isStartingDirector, isStoppingDirector: isStoppingDirector, directorStartBlockedReason: directorStartBlockedReason, onBackToMain: onBackToMain, onEnterDirectorWorkspace: onEnterDirectorWorkspace, onOpenSettings: onOpenSettings, onToggleDirector: onToggleDirector, executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase }), _jsx(LlmRuntimeOverlay, { activeView: "chief_engineer", websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount, pmRunning: pmRunning, directorRunning: directorRunning, llmState: llmRuntimeState.state, llmBlockedRoles: llmRuntimeState.blockedRoles, llmRequiredRoles: llmRuntimeState.requiredRoles, llmLastUpdated: llmRuntimeState.lastUpdated, currentPhase: currentPhase ?? '', qualityGate: qualityGate, executionLogs: executionLogs ?? [], llmStreamEvents: llmStreamEvents ?? [], processStreamEvents: processStreamEvents ?? [], fileEditEvents: fileEditEvents, controlPlaneProjection: controlPlaneProjection }), _jsx(Toaster, { position: "bottom-right" })] }));
}
