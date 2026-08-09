import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Resident 工作区页面组件
 *
 * 展示 AGI Resident 界面
 */
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { ResidentWorkspace } from '@/app/components/resident';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
/**
 * Resident 工作区页面
 */
export function ResidentPage({ workspace, onBackToMain, residentSnapshot, websocketLive, websocketReconnecting, websocketAttemptCount, pmRunning, directorRunning, llmRuntimeState, currentPhase, qualityGate, controlPlaneProjection, executionLogs, llmStreamEvents, processStreamEvents, notifyError, }) {
    return (_jsxs(ErrorBoundaryClass, { onError: (error) => notifyError(error.message || '发生未知错误'), children: [_jsx(ResidentWorkspace, { workspace: workspace, onBackToMain: onBackToMain, residentSnapshot: residentSnapshot, residentAgiLlmStatus: {
                    blocked: llmRuntimeState.blockedRoles.includes('resident_agi'),
                    unsupported: false,
                    lastUpdated: llmRuntimeState.lastUpdated,
                } }), _jsx(LlmRuntimeOverlay, { activeView: "agi", websocketLive: websocketLive, websocketReconnecting: websocketReconnecting, websocketAttemptCount: websocketAttemptCount, pmRunning: pmRunning, directorRunning: directorRunning, llmState: llmRuntimeState.state, llmBlockedRoles: llmRuntimeState.blockedRoles, llmRequiredRoles: llmRuntimeState.requiredRoles, llmLastUpdated: llmRuntimeState.lastUpdated, currentPhase: currentPhase ?? '', qualityGate: qualityGate, executionLogs: executionLogs ?? [], llmStreamEvents: llmStreamEvents ?? [], processStreamEvents: processStreamEvents ?? [], controlPlaneProjection: controlPlaneProjection }), _jsx(Toaster, { position: "bottom-right" })] }));
}
