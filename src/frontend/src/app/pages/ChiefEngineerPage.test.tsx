import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerPage } from './ChiefEngineerPage';

const chiefEngineerWorkspaceProps = vi.hoisted(() => vi.fn());
const runtimeOverlayProps = vi.hoisted(() => vi.fn());

vi.mock('@/app/components/chief-engineer', () => ({
  ChiefEngineerWorkspace: (props: {
    onOpenSettings?: () => void;
    onEnterDirectorWorkspace: () => void;
    directorStartBlockedReason?: string;
    isStoppingDirector?: boolean;
    executionLogs?: unknown[];
    llmStreamEvents?: unknown[];
    processStreamEvents?: unknown[];
    currentPhase?: string;
  }) => {
    chiefEngineerWorkspaceProps(props);
    return (
      <div>
        <button type="button" data-testid="chief-engineer-page-settings" onClick={props.onOpenSettings}>
          Settings
        </button>
        <button type="button" data-testid="chief-engineer-page-enter-director" onClick={props.onEnterDirectorWorkspace}>
          Director
        </button>
      </div>
    );
  },
}));

vi.mock('@/app/components/LlmRuntimeOverlay', () => ({
  LlmRuntimeOverlay: (props: { activeView: string; pmRunning?: boolean; directorRunning?: boolean }) => {
    runtimeOverlayProps(props);
    return <div data-testid="runtime-overlay">{props.activeView}</div>;
  },
}));

vi.mock('@/app/components/ui/sonner', () => ({
  Toaster: () => <div data-testid="toaster" />,
}));

function renderPage(overrides: Partial<Parameters<typeof ChiefEngineerPage>[0]> = {}): void {
  render(
    <ChiefEngineerPage
      workspace="C:/Temp/Product"
      engineStatus={null}
      tasks={[]}
      workers={[]}
      pmState={null}
      directorRunning={false}
      isStartingDirector={false}
      onBackToMain={vi.fn()}
      onEnterDirectorWorkspace={vi.fn()}
      onToggleDirector={vi.fn()}
      websocketLive={true}
      websocketReconnecting={false}
      websocketAttemptCount={0}
      llmRuntimeState={{
        state: 'READY',
        blockedRoles: [],
        requiredRoles: ['chief_engineer'],
        lastUpdated: '2026-05-23T00:00:00Z',
      }}
      notifyError={vi.fn()}
      {...overrides}
    />,
  );
}

describe('ChiefEngineerPage', () => {
  beforeEach(() => {
    chiefEngineerWorkspaceProps.mockClear();
    runtimeOverlayProps.mockClear();
  });

  it('forwards settings and Director navigation callbacks to ChiefEngineerWorkspace', () => {
    const onOpenSettings = vi.fn();
    const onEnterDirectorWorkspace = vi.fn();

    renderPage({ onOpenSettings, onEnterDirectorWorkspace });

    fireEvent.click(screen.getByTestId('chief-engineer-page-settings'));
    fireEvent.click(screen.getByTestId('chief-engineer-page-enter-director'));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(onEnterDirectorWorkspace).toHaveBeenCalledTimes(1);
    expect(chiefEngineerWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      onOpenSettings,
      onEnterDirectorWorkspace,
    }));
  });

  it('binds runtime overlay evidence to the Chief Engineer role view', () => {
    renderPage({ pmRunning: true, directorRunning: true });

    expect(screen.getByTestId('runtime-overlay')).toHaveTextContent('chief_engineer');
    expect(runtimeOverlayProps).toHaveBeenCalledWith(expect.objectContaining({
      activeView: 'chief_engineer',
      pmRunning: true,
      directorRunning: true,
    }));
  });

  it('forwards runtime stream evidence into the Chief Engineer workspace', () => {
    const executionLogs = [{ id: 'exec-1', timestamp: '2026-05-23T00:00:00Z', level: 'info', message: 'exec' }];
    const llmStreamEvents = [{ id: 'llm-1', timestamp: '2026-05-23T00:00:01Z', level: 'thinking', message: 'think' }];
    const processStreamEvents = [{ id: 'proc-1', timestamp: '2026-05-23T00:00:02Z', level: 'exec', message: 'tool' }];

    renderPage({
      executionLogs,
      llmStreamEvents,
      processStreamEvents,
      currentPhase: 'llm_calling',
    });

    expect(chiefEngineerWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      executionLogs,
      llmStreamEvents,
      processStreamEvents,
      currentPhase: 'llm_calling',
    }));
  });

  it('forwards the Director stopping transition into the Chief Engineer workspace', () => {
    renderPage({
      directorRunning: true,
      isStoppingDirector: true,
    });

    expect(chiefEngineerWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      directorRunning: true,
      isStartingDirector: false,
      isStoppingDirector: true,
    }));
  });

  it('passes AGENTS review blockers into the Chief Engineer Director gate', () => {
    const { rerender } = render(
      <ChiefEngineerPage
        workspace="C:/Temp/Product"
        engineStatus={null}
        tasks={[]}
        workers={[]}
        pmState={null}
        directorRunning={false}
        isStartingDirector={false}
        agentsRequired
        agentsDraftReady={false}
        agentsDraftFailed={false}
        onBackToMain={vi.fn()}
        onEnterDirectorWorkspace={vi.fn()}
        onToggleDirector={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['chief_engineer', 'director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(chiefEngineerWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      directorStartBlockedReason: 'AGENTS.md 审核未完成，等待草稿生成或人工确认后才能启动 Director。',
    }));

    rerender(
      <ChiefEngineerPage
        workspace="C:/Temp/Product"
        engineStatus={null}
        tasks={[]}
        workers={[]}
        pmState={null}
        directorRunning={false}
        isStartingDirector={false}
        agentsRequired
        agentsDraftReady={false}
        agentsDraftFailed
        onBackToMain={vi.fn()}
        onEnterDirectorWorkspace={vi.fn()}
        onToggleDirector={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['chief_engineer', 'director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(chiefEngineerWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      directorStartBlockedReason: 'AGENTS 草稿生成失败，请返回主界面重新生成或人工处理后再启动 Director。',
    }));
  });

  it('passes Director LLM readiness blocks into the Chief Engineer workspace', () => {
    renderPage({
      llmRuntimeState: {
        state: 'BLOCKED',
        blockedRoles: ['director'],
        requiredRoles: ['chief_engineer', 'director'],
        lastUpdated: '2026-05-23T00:00:00Z',
      },
    });

    expect(chiefEngineerWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      directorStartBlockedReason: 'LLM 就绪检查未通过：Director 角色当前绑定的 provider/model 没有通过真实测试。',
    }));
  });

  it('prefers the app-level Director start blocker over local role checks', () => {
    renderPage({
      directorStartBlockedReason: 'LanceDB unavailable: lock timeout',
      agentsRequired: true,
      agentsDraftReady: false,
      llmRuntimeState: {
        state: 'BLOCKED',
        blockedRoles: ['director'],
        requiredRoles: ['chief_engineer', 'director'],
        lastUpdated: '2026-05-23T00:00:00Z',
      },
    });

    expect(chiefEngineerWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      directorStartBlockedReason: 'LanceDB unavailable: lock timeout',
    }));
  });
});
