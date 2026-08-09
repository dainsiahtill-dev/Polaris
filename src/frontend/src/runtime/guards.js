/**
 * Runtime Guards - 运行时状态守卫
 *
 * 纯函数，用于检查运行时状态的各种条件
 */
import { TaskStatus } from '@/types/task';
// ============================================================
// 连接状态守卫
// ============================================================
export function guardIsConnected(state) {
    return state.live === true;
}
export function guardIsReconnecting(state) {
    return state.reconnecting === true;
}
export function hasConnectionError(state) {
    return state.error !== null && state.error !== '';
}
// ============================================================
// PM 状态守卫
// ============================================================
export function isPmRunning(state) {
    if (!state.pmStatus)
        return false;
    return state.pmStatus.running === true;
}
export function isPmIdle(state) {
    return !isPmRunning(state);
}
export function getPmStateToken(state) {
    const status = state.pmStatus;
    if (!status)
        return '';
    const root = status;
    const nested = typeof root.status === 'object' ? root.status : null;
    const deepNested = nested && typeof nested.status === 'object' ? nested.status : null;
    const token = String(deepNested?.state || nested?.state || root?.state || '').toLowerCase().trim() ||
        String(deepNested?.status || nested?.status || root?.status || '').toLowerCase().trim();
    return token;
}
// ============================================================
// Director 状态守卫
// ============================================================
export function isDirectorRunning(state) {
    if (!state.directorStatus)
        return false;
    const root = state.directorStatus;
    const nested = root && typeof root.status === 'object' ? root.status : null;
    const deepNested = nested && typeof nested.status === 'object' ? nested.status : null;
    const explicitRunning = state.directorStatus.running === true;
    const tokenState = String(deepNested?.state || nested?.state || root?.state || '').toUpperCase();
    return explicitRunning || tokenState === 'RUNNING';
}
export function isDirectorIdle(state) {
    return !isDirectorRunning(state);
}
export function getDirectorStateToken(state) {
    const status = state.directorStatus;
    if (!status)
        return '';
    const root = status;
    const nested = root && typeof root.status === 'object' ? root.status : null;
    const deepNested = nested && typeof nested.status === 'object' ? nested.status : null;
    return String(deepNested?.state || nested?.state || root?.state || '').toLowerCase().trim();
}
export function isDirectorFailed(state) {
    const token = getDirectorStateToken(state);
    return token === 'failed' || token === 'error' || token === 'deadlock';
}
// ============================================================
// LanceDB 状态守卫
// ============================================================
export function isLancedbBlocked(state) {
    const status = state.lancedbStatus;
    if (!status)
        return false;
    const root = status;
    return root?.blocked === true || root?.error !== undefined;
}
export function isLancedbReady(state) {
    const status = state.lancedbStatus;
    if (!status)
        return false;
    const root = status;
    return root?.ready === true || root?.healthy === true;
}
// ============================================================
// Anthro/文档状态守卫
// ============================================================
export function isDocsMissing(state) {
    const anthro = state.anthroState;
    if (!anthro)
        return true;
    const root = anthro;
    const docsReady = root?.docs_ready;
    if (typeof docsReady === 'boolean') {
        return !docsReady;
    }
    const docsStatus = String(root?.docs_status || '').toLowerCase();
    return docsStatus !== 'ready' && docsStatus !== 'complete' && docsStatus !== 'completed';
}
export function hasAnthroState(state) {
    return state.anthroState !== null;
}
// ============================================================
// 任务状态守卫
// ============================================================
export function hasTasks(state) {
    return state.tasks.length > 0;
}
export function hasCompletedTasks(state) {
    return state.tasks.some(task => task.done === true ||
        task.completed === true ||
        task.status === TaskStatus.COMPLETED ||
        task.status === TaskStatus.SUCCESS);
}
export function hasFailedTasks(state) {
    return state.tasks.some(task => task.status === TaskStatus.FAILED);
}
export function getTaskCount(state) {
    return state.tasks.length;
}
export function getCompletedTaskCount(state) {
    return state.tasks.filter(task => task.done === true ||
        task.completed === true ||
        task.status === TaskStatus.COMPLETED ||
        task.status === TaskStatus.SUCCESS).length;
}
export function getFailedTaskCount(state) {
    return state.tasks.filter(task => task.status === TaskStatus.FAILED).length;
}
// ============================================================
// 质量门禁守卫
// ============================================================
export function hasQualityGate(state) {
    return state.qualityGate !== null;
}
export function isQualityGatePassed(state) {
    const qg = state.qualityGate;
    if (!qg)
        return false;
    return qg.passed === true;
}
export function isQualityGateFailed(state) {
    const qg = state.qualityGate;
    if (!qg)
        return false;
    return qg.passed === false;
}
export function getQualityGateScore(state) {
    const qg = state.qualityGate;
    return qg?.score ?? 0;
}
export function hasCriticalIssues(state) {
    const qg = state.qualityGate;
    if (!qg)
        return false;
    return qg.issues?.some(issue => issue.type === 'critical') ?? false;
}
// ============================================================
// 阶段守卫
// ============================================================
export function isPhasePlanning(phase) {
    const normalized = phase.toLowerCase().trim();
    return normalized === 'planning' || normalized === 'pm_planning';
}
export function isPhaseExecuting(phase) {
    const normalized = phase.toLowerCase().trim();
    return normalized === 'executing' ||
        normalized === 'implementation' ||
        normalized === 'tool_running' ||
        normalized === 'llm_calling' ||
        normalized.startsWith('director_');
}
export function isPhaseVerification(phase) {
    const normalized = phase.toLowerCase().trim();
    return normalized === 'verification' ||
        normalized === 'qa_gate' ||
        normalized.startsWith('qa_');
}
export function isPhaseCompleted(phase) {
    const normalized = phase.toLowerCase().trim();
    return normalized === 'completed' ||
        normalized === 'done' ||
        normalized === 'success' ||
        normalized === 'handover';
}
export function isPhaseError(phase) {
    const normalized = phase.toLowerCase().trim();
    return normalized === 'error' ||
        normalized === 'failed' ||
        normalized === 'blocked';
}
// ============================================================
// LLM 阻塞守卫
// ============================================================
export function isLlmBlocked(state) {
    const engine = state.engineStatus;
    if (!engine)
        return false;
    const root = engine;
    const blocked = root?.llm_blocked || root?.blocked || root?.llm_blocked;
    return blocked === true;
}
export function hasLlmProvider(state) {
    const engine = state.engineStatus;
    if (!engine)
        return false;
    const root = engine;
    const provider = root?.provider || root?.llm_provider;
    return provider !== undefined && provider !== null && String(provider).trim() !== '';
}
export function evaluateRuntimeGuards(state) {
    const blockers = [];
    const warnings = [];
    if (!guardIsConnected(state)) {
        blockers.push('WebSocket 未连接');
    }
    else if (hasConnectionError(state)) {
        blockers.push(`连接错误: ${state.error}`);
    }
    if (isLancedbBlocked(state)) {
        blockers.push('LanceDB 被阻塞');
    }
    if (isDocsMissing(state)) {
        blockers.push('项目文档缺失');
    }
    if (isLlmBlocked(state)) {
        warnings.push('LLM 调用被阻塞');
    }
    if (hasCriticalIssues(state)) {
        blockers.push('质量门禁存在严重问题');
    }
    if (isDirectorFailed(state)) {
        warnings.push('Director 执行失败');
    }
    const ready = blockers.length === 0;
    return { ready, blockers, warnings };
}
export function getRequiredAgents(state, currentPhase) {
    const phase = currentPhase.toLowerCase().trim();
    return {
        pm: isPhasePlanning(phase) || isDocsMissing(state),
        director: isPhaseExecuting(phase) || hasTasks(state),
        qa: isPhaseVerification(phase) || isPhaseCompleted(phase),
    };
}
export function isAgentRequired(agent, state, currentPhase) {
    const required = getRequiredAgents(state, currentPhase);
    return required[agent];
}
