import type { BackendStatus, RuntimeIssue, SnapshotPayload } from './types/appContracts';

export function isPmTerminalFailure(status: BackendStatus | null): boolean {
  if (!status) return false;
  if (status.running === true) return false;
  const statusToken = String(status.status || '').trim().toLowerCase();
  const exitCode = typeof status.exit_code === 'number' ? status.exit_code : null;
  const error = String(status.error || '').trim();
  return (
    (exitCode !== null && exitCode !== 0)
    || status.ok === false
    || (status.terminal === true && statusToken === 'failed')
    || Boolean(error)
  );
}

export function isPmTerminalSuccess(status: BackendStatus | null): boolean {
  if (!status || status.running === true || status.terminal !== true) return false;
  const statusToken = String(status.status || '').trim().toLowerCase();
  const exitCode = typeof status.exit_code === 'number' ? status.exit_code : null;
  const error = String(status.error || '').trim();
  if (error || status.ok === false || statusToken === 'failed') return false;
  return status.ok === true || statusToken === 'success' || statusToken === 'completed' || exitCode === 0;
}

function isPmRuntimeIssue(issue: RuntimeIssue | null): boolean {
  if (!issue) return false;
  const token = `${issue.code || ''} ${issue.title || ''}`.toUpperCase();
  return (
    token.includes('PM')
    || token.includes('ENGINE_RUNTIME_FAILED')
    || token.includes('POLARIS 引擎执行失败')
  );
}

function isResolvedPmIterationIssue(issue: RuntimeIssue | null): boolean {
  if (!issue) return false;
  const token = `${issue.code || ''} ${issue.title || ''} ${issue.detail || ''}`.toUpperCase();
  return (
    token.includes('PM_ITERATION_FAILED')
    || token.includes('PM ITERATION FAILED')
    || token.includes('QA BLOCKED BECAUSE PM ITERATION FAILED')
  );
}

export function shouldSuppressRuntimeIssueAfterPmSuccess(
  status: BackendStatus | null,
  issue: RuntimeIssue | null,
): boolean {
  return isPmTerminalSuccess(status) && isResolvedPmIterationIssue(issue);
}

export function resolveEffectivePmRunning(status: BackendStatus | null, issue: RuntimeIssue | null): boolean {
  if (status?.running === true) {
    return true;
  }
  if (isPmTerminalFailure(status) || isPmRuntimeIssue(issue)) {
    return false;
  }
  return Boolean(status?.running);
}

export function resolveEffectivePhase(
  currentPhase: string,
  pmRunning: boolean,
  issue: RuntimeIssue | null,
  directorRunning = false,
): string {
  const systemRunning = pmRunning || directorRunning;
  if (!systemRunning && isPmRuntimeIssue(issue)) return 'error';
  const token = String(currentPhase || '').trim().toLowerCase();
  if (!systemRunning && !issue && ['error', 'failed', 'blocked', 'cancelled', 'canceled'].includes(token)) {
    return 'idle';
  }
  if (!systemRunning && ['planning', 'analyzing', 'llm_calling'].includes(token)) {
    return 'idle';
  }
  return currentPhase;
}

export function isSnapshotDocsMissing(
  snapshot: Pick<SnapshotPayload, 'docs_present' | 'workspace_status'> | null | undefined,
): boolean {
  if (snapshot?.docs_present === true) return false;
  if (snapshot?.docs_present === false) return true;
  return snapshot?.workspace_status?.status === 'NEEDS_DOCS_INIT';
}

export function isWorkspaceDocsMissing(
  ...snapshots: Array<Pick<SnapshotPayload, 'docs_present' | 'workspace_status'> | null | undefined>
): boolean {
  if (snapshots.some((candidate) => candidate?.docs_present === true)) {
    return false;
  }
  if (snapshots.some((candidate) => candidate?.docs_present === false)) {
    return true;
  }
  return snapshots.some((candidate) => candidate?.workspace_status?.status === 'NEEDS_DOCS_INIT');
}

export function shouldIncomingSnapshotClearDocsBlocker(
  previous: Pick<SnapshotPayload, 'docs_present' | 'workspace_status'> | null | undefined,
  incoming: Pick<SnapshotPayload, 'docs_present' | 'workspace_status'> | null | undefined,
): boolean {
  if (!previous || !incoming) return false;
  return isSnapshotDocsMissing(previous) && incoming.docs_present === true && !isSnapshotDocsMissing(incoming);
}
