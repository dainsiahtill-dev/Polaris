export function isPmTerminalFailure(status) {
    if (!status)
        return false;
    if (status.running === true)
        return false;
    const statusToken = String(status.status || '').trim().toLowerCase();
    const exitCode = typeof status.exit_code === 'number' ? status.exit_code : null;
    const error = String(status.error || '').trim();
    return ((exitCode !== null && exitCode !== 0)
        || status.ok === false
        || (status.terminal === true && statusToken === 'failed')
        || Boolean(error));
}
export function isPmTerminalSuccess(status) {
    if (!status || status.running === true || status.terminal !== true)
        return false;
    const statusToken = String(status.status || '').trim().toLowerCase();
    const exitCode = typeof status.exit_code === 'number' ? status.exit_code : null;
    const error = String(status.error || '').trim();
    if (error || status.ok === false || statusToken === 'failed')
        return false;
    return status.ok === true || statusToken === 'success' || statusToken === 'completed' || exitCode === 0;
}
function isPmRuntimeIssue(issue) {
    if (!issue)
        return false;
    const token = `${issue.code || ''} ${issue.title || ''}`.toUpperCase();
    return (token.includes('PM')
        || token.includes('ENGINE_RUNTIME_FAILED')
        || token.includes('POLARIS 引擎执行失败'));
}
function isResolvedPmIterationIssue(issue) {
    if (!issue)
        return false;
    const token = `${issue.code || ''} ${issue.title || ''} ${issue.detail || ''}`.toUpperCase();
    return (token.includes('PM_ITERATION_FAILED')
        || token.includes('PM ITERATION FAILED')
        || token.includes('QA BLOCKED BECAUSE PM ITERATION FAILED'));
}
export function shouldSuppressRuntimeIssueAfterPmSuccess(status, issue) {
    return isPmTerminalSuccess(status) && isResolvedPmIterationIssue(issue);
}
export function resolveEffectivePmRunning(status, issue) {
    if (status?.running === true) {
        return true;
    }
    if (isPmTerminalFailure(status) || isPmRuntimeIssue(issue)) {
        return false;
    }
    return Boolean(status?.running);
}
export function resolveEffectivePhase(currentPhase, pmRunning, issue, directorRunning = false) {
    const systemRunning = pmRunning || directorRunning;
    if (!systemRunning && isPmRuntimeIssue(issue))
        return 'error';
    const token = String(currentPhase || '').trim().toLowerCase();
    if (!systemRunning && !issue && ['error', 'failed', 'blocked', 'cancelled', 'canceled'].includes(token)) {
        return 'idle';
    }
    if (!systemRunning && ['planning', 'analyzing', 'llm_calling'].includes(token)) {
        return 'idle';
    }
    return currentPhase;
}
export function isSnapshotDocsMissing(snapshot) {
    if (snapshot?.docs_present === true)
        return false;
    if (snapshot?.docs_present === false)
        return true;
    return snapshot?.workspace_status?.status === 'NEEDS_DOCS_INIT';
}
export function isWorkspaceDocsMissing(...snapshots) {
    if (snapshots.some((candidate) => candidate?.docs_present === true)) {
        return false;
    }
    if (snapshots.some((candidate) => candidate?.docs_present === false)) {
        return true;
    }
    return snapshots.some((candidate) => candidate?.workspace_status?.status === 'NEEDS_DOCS_INIT');
}
export function shouldIncomingSnapshotClearDocsBlocker(previous, incoming) {
    if (!previous || !incoming)
        return false;
    return isSnapshotDocsMissing(previous) && incoming.docs_present === true && !isSnapshotDocsMissing(incoming);
}
