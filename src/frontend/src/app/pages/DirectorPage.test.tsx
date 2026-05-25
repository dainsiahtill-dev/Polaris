import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectorPage } from './DirectorPage';

const directorWorkspaceProps = vi.hoisted(() => vi.fn());
const runtimeOverlayProps = vi.hoisted(() => vi.fn());
const DIRECTOR_LLM_BLOCKED_REASON =
  'LLM 就绪检查未通过：Director 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。';

vi.mock('@/app/components/director', () => ({
  DirectorWorkspace: (props: Record<string, unknown>) => {
    directorWorkspaceProps(props);
    return <div data-testid="director-page-workspace" />;
  },
}));

vi.mock('@/app/components/LlmRuntimeOverlay', () => ({
  LlmRuntimeOverlay: (props: Record<string, unknown>) => {
    runtimeOverlayProps(props);
    return <div data-testid="runtime-overlay">{String(props.activeView)}</div>;
  },
}));

vi.mock('@/app/components/ui/sonner', () => ({
  Toaster: () => <div data-testid="toaster" />,
}));

describe('DirectorPage', () => {
  beforeEach(() => {
    directorWorkspaceProps.mockClear();
    runtimeOverlayProps.mockClear();
  });

  it('forwards workspace evidence and cross-role runtime state', () => {
    const fileEditEvents = [{ task_id: 'D-1', path: 'src/app.ts' }];
    const taskTraceMap = new Map([['D-1', [{ phase: 'tool_running' }]]]);
    const taskProgressMap = new Map([['D-1', { phase: 'executing', phaseIndex: 1, phaseTotal: 3 }]]);

    render(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={true}
        pmRunning={true}
        isStarting={false}
        isStopping={false}
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        fileEditEvents={fileEditEvents}
        taskTraceMap={taskTraceMap}
        taskProgressMap={taskProgressMap}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(screen.getByTestId('director-page-workspace')).toBeInTheDocument();
    expect(directorWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      fileEditEvents,
      taskTraceMap,
      taskProgressMap,
    }));
    expect(runtimeOverlayProps).toHaveBeenCalledWith(expect.objectContaining({
      activeView: 'director',
      pmRunning: true,
      directorRunning: true,
      fileEditEvents,
    }));
  });

  it('forwards the Director stopping transition to the workspace shell', () => {
    render(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={true}
        isStarting={false}
        isStopping={true}
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(directorWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      directorRunning: true,
      isStarting: false,
      isStopping: true,
    }));
  });

  it('passes AGENTS and LLM start blockers into the Director workspace', () => {
    const { rerender } = render(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={false}
        isStarting={false}
        isStopping={false}
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        agentsRequired
        agentsDraftReady={false}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(directorWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      startBlockedReason: 'AGENTS.md 审核未完成，等待草稿生成或人工确认后才能启动 Director。',
    }));

    rerender(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={false}
        isStarting={false}
        isStopping={false}
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        agentsRequired
        agentsDraftReady={false}
        agentsDraftFailed
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(directorWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      startBlockedReason: 'AGENTS 草稿生成失败，请返回主界面重新生成或人工处理后再启动 Director。',
    }));

    rerender(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={false}
        isStarting={false}
        isStopping={false}
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        agentsRequired={false}
        agentsDraftReady={false}
        llmRuntimeState={{
          state: 'BLOCKED',
          blockedRoles: ['director'],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(directorWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      startBlockedReason: DIRECTOR_LLM_BLOCKED_REASON,
    }));
  });

  it('prefers the app-level Director start blocker over local role checks', () => {
    render(
      <DirectorPage
        workspace="C:/Temp/Product"
        tasks={[]}
        workers={[]}
        directorRunning={false}
        isStarting={false}
        isStopping={false}
        directorStartBlockedReason="docs/ 初始化未完成"
        onToggleDirector={vi.fn()}
        onBackToMain={vi.fn()}
        websocketLive={true}
        websocketReconnecting={false}
        websocketAttemptCount={0}
        agentsRequired
        agentsDraftReady={false}
        llmRuntimeState={{
          state: 'BLOCKED',
          blockedRoles: ['director'],
          requiredRoles: ['director'],
          lastUpdated: '2026-05-23T00:00:00Z',
        }}
        notifyError={vi.fn()}
      />,
    );

    expect(directorWorkspaceProps).toHaveBeenLastCalledWith(expect.objectContaining({
      startBlockedReason: 'docs/ 初始化未完成',
    }));
  });
});
