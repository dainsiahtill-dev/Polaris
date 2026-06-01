import { describe, expect, it } from 'vitest';

import {
  isPmTerminalSuccess,
  isSnapshotDocsMissing,
  isWorkspaceDocsMissing,
  resolveEffectivePhase,
  resolveEffectivePmRunning,
  shouldIncomingSnapshotClearDocsBlocker,
  shouldSuppressRuntimeIssueAfterPmSuccess,
} from './runtimeState';
import type { BackendStatus, RuntimeIssue } from './types/appContracts';

describe('App PM runtime state resolution', () => {
  it('keeps a live PM run active when a stale runtime issue is still present', () => {
    const status: BackendStatus = {
      running: true,
      terminal: false,
      status: 'running',
      error: 'PM_ITERATION_FAILED',
    };
    const staleIssue: RuntimeIssue = {
      code: 'PM_ITERATION_FAILED',
      title: 'Polaris 引擎执行失败',
      detail: 'PM: previous iteration failed',
    };

    expect(resolveEffectivePmRunning(status, staleIssue)).toBe(true);
    expect(resolveEffectivePhase('planning', true, staleIssue)).toBe('planning');
  });

  it('resets a stale terminal phase when no role is running and no current issue exists', () => {
    expect(resolveEffectivePhase('error', false, null)).toBe('idle');
    expect(resolveEffectivePhase('failed', false, null)).toBe('idle');
    expect(resolveEffectivePhase('blocked', false, null)).toBe('idle');
  });

  it('keeps a terminal phase while Director is actively running', () => {
    expect(resolveEffectivePhase('error', false, null, true)).toBe('error');
  });

  it('still marks terminal PM failures as inactive when there is no active run', () => {
    const status: BackendStatus = {
      running: false,
      terminal: true,
      status: 'failed',
      error: 'PM_ITERATION_FAILED',
    };

    expect(resolveEffectivePmRunning(status, null)).toBe(false);
  });

  it('treats a latest successful PM terminal status as a resolved run', () => {
    const status: BackendStatus = {
      running: false,
      terminal: true,
      status: 'success',
      ok: true,
      exit_code: 0,
      error: '',
    };

    expect(isPmTerminalSuccess(status)).toBe(true);
  });

  it('suppresses stale PM iteration failures after the latest PM run succeeds', () => {
    const status: BackendStatus = {
      running: false,
      terminal: true,
      status: 'success',
      ok: true,
      exit_code: 0,
      error: '',
    };
    const staleIssue: RuntimeIssue = {
      code: 'PM_ITERATION_FAILED',
      title: 'Polaris 引擎执行失败',
      detail: 'Director dispatch skipped because PM iteration failed\nQA blocked because PM iteration failed',
    };

    expect(shouldSuppressRuntimeIssueAfterPmSuccess(status, staleIssue)).toBe(true);
  });

  it('does not suppress unrelated Director failures after PM planning succeeds', () => {
    const status: BackendStatus = {
      running: false,
      terminal: true,
      status: 'success',
      ok: true,
      exit_code: 0,
      error: '',
    };
    const directorIssue: RuntimeIssue = {
      code: 'DIRECTOR_WORKFLOW_FAILED',
      title: 'Director 链路异常',
      detail: 'Director workflow failed while applying code changes',
    };

    expect(shouldSuppressRuntimeIssueAfterPmSuccess(status, directorIssue)).toBe(false);
  });

  it('lets docs_present=true override a stale docs init workspace status', () => {
    expect(isSnapshotDocsMissing({
      docs_present: true,
      workspace_status: { status: 'NEEDS_DOCS_INIT' },
    })).toBe(false);
  });

  it('keeps docs init blocked when snapshot explicitly reports docs missing', () => {
    expect(isSnapshotDocsMissing({
      docs_present: false,
      workspace_status: { status: 'READY' },
    })).toBe(true);
  });

  it('lets a fresh docs_present=true snapshot clear an older docs init blocker', () => {
    expect(isWorkspaceDocsMissing(
      {
        docs_present: false,
        workspace_status: { status: 'NEEDS_DOCS_INIT' },
      },
      {
        docs_present: true,
        workspace_status: { status: 'READY' },
      },
    )).toBe(false);
  });

  it('prevents a stale rich snapshot from keeping docs init blocked after docs become ready', () => {
    expect(shouldIncomingSnapshotClearDocsBlocker(
      {
        docs_present: false,
        workspace_status: { status: 'NEEDS_DOCS_INIT' },
      },
      {
        docs_present: true,
        workspace_status: { status: 'READY' },
      },
    )).toBe(true);
  });
});
