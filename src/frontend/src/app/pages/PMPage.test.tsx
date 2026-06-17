import { render, screen, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PMPage } from './PMPage';

const pmWorkspaceProps = vi.hoisted(() => vi.fn());
const runtimeOverlayProps = vi.hoisted(() => vi.fn());

vi.mock('@/app/components/pm', () => ({
  PMWorkspace: (props: { onOpenSettings?: () => void }) => {
    pmWorkspaceProps(props);
    return (
      <button type="button" data-testid="pm-page-settings" onClick={props.onOpenSettings}>
        Settings
      </button>
    );
  },
}));

vi.mock('@/app/components/LlmRuntimeOverlay', () => ({
  LlmRuntimeOverlay: (props: Record<string, unknown>) => {
    runtimeOverlayProps(props);
    return <div data-testid="runtime-overlay" />;
  },
}));

vi.mock('@/app/components/ui/sonner', () => ({
  Toaster: () => <div data-testid="toaster" />,
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => ({
    subscribeChannels: () => () => {},
    registerMessageHandler: () => () => {},
  }),
}));

function renderPage(
  onOpenSettings = vi.fn(),
  overrides: Partial<Parameters<typeof PMPage>[0]> = {},
): void {
  render(
    <PMPage
      workspace="C:/Temp/Product"
      tasks={[]}
      pmState={null}
      pmRunning={false}
      isStarting={false}
      onTogglePm={vi.fn()}
      onRunPmOnce={vi.fn()}
      onBackToMain={vi.fn()}
      onOpenSettings={onOpenSettings}
      websocketLive={true}
      websocketReconnecting={false}
      websocketAttemptCount={0}
      llmRuntimeState={{
        state: 'READY',
        blockedRoles: [],
        requiredRoles: ['pm'],
        lastUpdated: '2026-05-23T00:00:00Z',
      }}
      notifyError={vi.fn()}
      {...overrides}
    />,
  );
}

describe('PMPage', () => {
  beforeEach(() => {
    pmWorkspaceProps.mockClear();
    runtimeOverlayProps.mockClear();
  });

  it('forwards the settings callback to PMWorkspace', () => {
    const onOpenSettings = vi.fn();

    renderPage(onOpenSettings);

    fireEvent.click(screen.getByTestId('pm-page-settings'));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(pmWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({ onOpenSettings }));
  });

  it('forwards PM runtime evidence and cross-role overlay state', () => {
    const pmTerminalStatus = { terminal: true, status: 'failed', exit_code: 1 };
    const runtimeIssue = { code: 'PM_FAILED', title: 'PM failed', detail: 'see logs' };
    const taskTraceMap = new Map([['PM-1', [{ phase: 'planning' }]]]);
    const fileEditEvents = [{ task_id: 'PM-1', path: 'docs/plan.md' }];

    renderPage(vi.fn(), {
      directorRunning: true,
      pmTerminalStatus,
      pmStartBlockedReason: 'LLM blocked',
      runtimeIssue,
      qualityGate: { score: 90 },
      taskTraceMap,
      fileEditEvents,
    });

    expect(pmWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      pmTerminalStatus,
      pmStartBlockedReason: 'LLM blocked',
      runtimeIssue,
      qualityGate: { score: 90 },
      taskTraceMap,
    }));
    expect(runtimeOverlayProps).toHaveBeenCalledWith(expect.objectContaining({
      activeView: 'pm',
      pmRunning: false,
      directorRunning: true,
      fileEditEvents,
    }));
  });

  it('forwards the PM stopping transition to PMWorkspace', () => {
    renderPage(vi.fn(), {
      pmRunning: true,
      isStopping: true,
    });

    expect(pmWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      pmRunning: true,
      isStarting: false,
      isStopping: true,
    }));
  });
});
