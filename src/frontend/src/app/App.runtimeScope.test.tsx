import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const harness = vi.hoisted(() => ({
  subscribeChannels: vi.fn((_channels: { channel: string }[]) => vi.fn()),
  reconnect: vi.fn(),
  registerMessageHandler: vi.fn(() => vi.fn()),
  sendCommand: vi.fn(),
  setConnectionState: vi.fn(),
  resetForWorkspace: vi.fn(),
  settings: { workspace: '/workspace/main' },
  actions: new Proxy({}, { get: () => () => undefined }),
  empty: [],
  factoryBench: { currentSession: null, events: [] },
  factoryBenchOptions: vi.fn(),
  factoryOptions: vi.fn(),
  start: vi.fn(), get: vi.fn(), cancel: vi.fn(), resume: vi.fn(), discuss: vi.fn(),
}));

// Render the actual App and run its options through the actual connection hook.
// UI panels and external reads are isolated; channel selection stays production code.
vi.mock('@/hooks', () => ({
  useSettings: () => ({ settings: harness.settings, load: harness.reconnect }),
  useUIState: () => ({ state: {}, actions: harness.actions }),
  useNotifications: () => ({ notifications: harness.empty }),
  useProcessOperations: () => ({}),
  useFileManager: () => ({}),
  useMemos: () => ({ memoData: {} }),
  useMemory: () => ({ memoryData: {} }),
  useAgentsReview: () => ({}),
  useBackendHealthPing: () => ({}),
}));
vi.mock('@/app/hooks/useRuntimeStore', () => ({
  useRuntimeStore: (selector: (state: unknown) => unknown) => selector({
    setConnectionState: harness.setConnectionState,
    resetForWorkspace: harness.resetForWorkspace,
  }),
}));
vi.mock('@/runtime/transport', () => ({
  RuntimeTransportProvider: ({ children }: { children: React.ReactNode }) => children,
  useConnectionState: () => ({ connected: true, reconnecting: false, attemptCount: 0, error: null }),
  useTransportActions: () => ({
    subscribeChannels: harness.subscribeChannels,
    reconnect: harness.reconnect,
    sendCommand: harness.sendCommand,
    getLastCursor: () => 0,
  }),
  useMessageHandler: () => ({ registerMessageHandler: harness.registerMessageHandler }),
}));
vi.mock('./hooks/useRuntime', async () => {
  const { useRuntimeConnection } = await import('./hooks/useRuntimeConnection');
  return {
    useRuntime: (options: Parameters<typeof useRuntimeConnection>[0]) => ({
      ...useRuntimeConnection(options),
      snapshot: null,
      tasks: harness.empty,
      workers: harness.empty,
      dialogueEvents: harness.empty,
      executionLogs: harness.empty,
      llmStreamEvents: harness.empty,
      processStreamEvents: harness.empty,
      fileEditEvents: harness.empty,
      runId: null,
      currentPhase: 'idle',
    }),
  };
});
vi.mock('./hooks/useConnectionNotifications', () => ({ useRuntimeConnectionNotifications: () => undefined }));
vi.mock('./hooks/useUsageStats', () => ({ useUsageStats: () => ({ stats: {} }) }));
vi.mock('./hooks/useLlmRuntimeGate', () => ({
  useLlmRuntimeGate: () => ({ llmRuntimeState: { state: 'UNKNOWN', blockedRoles: [] }, getLlmRoleBlockedReason: () => '' }),
}));
vi.mock('@/hooks/useFactory', () => ({ useFactory: (options: unknown) => { harness.factoryOptions(options); return { currentRun: null }; } }));
vi.mock('@/services/engineeringRunService', () => ({ startEngineeringRun: harness.start, getEngineeringRun: harness.get, cancelEngineeringRun: harness.cancel, resumeEngineeringRun: harness.resume, discussEngineeringRole: harness.discuss }));
vi.mock('@/hooks/useFactoryBench', () => ({ useFactoryBench: (options: unknown) => { harness.factoryBenchOptions(options); return harness.factoryBench; } }));
vi.mock('@/services/controlPlane', () => ({
  getControlPlaneProjection: async () => ({ ok: true }),
  controlPlaneProjectionFromRuntimeMessage: () => null,
}));
vi.mock('@/api', () => ({ openPath: vi.fn(), pickWorkspace: vi.fn() }));
vi.mock('@/services', () => ({ runtimeService: {} }));
vi.mock('react-resizable-panels', () => ({ Panel: () => null, PanelGroup: () => null, PanelResizeHandle: () => null }));
vi.mock('./components/ui/sonner', () => ({ Toaster: () => null }));
vi.mock('@/app/components/ControlPanel', () => ({ ControlPanel: () => null }));
vi.mock('@/app/components/RealTimeStatusBar', () => ({ RealTimeStatusBar: () => null }));
vi.mock('@/app/components/ContextSidebar', () => ({ ContextSidebar: () => null }));
vi.mock('@/app/components/ProjectProgressPanel', () => ({ ProjectProgressPanel: () => null }));
vi.mock('@/app/components/HistoryDrawer', () => ({ HistoryDrawer: () => null }));
vi.mock('@/app/components/EnhancedNotificationManager', () => ({ EnhancedNotificationManager: () => null }));
vi.mock('@/app/components/AgentsReviewDialog', () => ({ AgentsReviewDialog: () => null }));
vi.mock('@/app/components/ErrorBoundary', () => ({ ErrorBoundaryClass: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('@/app/components/RuntimeErrorDialog', () => ({ RuntimeErrorDialog: () => null }));
vi.mock('@/app/pages', () => ({ ChiefEngineerPage: () => null, DirectorPage: () => null, PMPage: () => null }));
vi.mock('@/app/components/factory/FactoryWorkspace', () => ({ FactoryWorkspace: () => null }));
vi.mock('@/app/components/factory/BenchStatusStrip', () => ({ BenchStatusStrip: () => null }));
vi.mock('@/app/components/resident', () => ({ ResidentWorkspace: () => null }));
vi.mock('@/app/components/LlmRuntimeOverlay', () => ({ LlmRuntimeOverlay: () => null }));
vi.mock('@/app/components/RuntimeDiagnosticsWorkspace', () => ({ RuntimeDiagnosticsWorkspace: () => null }));
vi.mock('@/app/components/contextos', () => ({ ContextOSWorkspace: () => null }));
vi.mock('@/app/components/workspace-files/WorkspaceFilesPage', () => ({ WorkspaceFilesPage: () => null }));
vi.mock('@/app/launcher/LauncherWorkspace', () => ({ LauncherWorkspace: () => null }));
vi.mock('./components/SettingsModal', () => ({ SettingsModal: () => null }));

import App from './App';

function runtimeSubscriptions(): string[][] {
  return harness.subscribeChannels.mock.calls.map(([channels]) =>
    (channels as unknown as { channel: string }[]).map(({ channel }) => channel),
  ).filter((channels) => channels.includes('event.factory'));
}

describe('App runtime global observer authorization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv('DEV', true);
    vi.stubEnv('VITE_POLARIS_INTERNAL_BENCH', '1');
    vi.stubEnv('VITE_POLARIS_GLOBAL_BENCH_OBSERVER', '');
    vi.stubEnv('VITE_POLARIS_WORKSPACE', '');
    vi.stubEnv('VITE_WORKSPACE', '');
    vi.stubEnv('VITE_POLARIS_INSTANCE_ID', '');
    window.history.replaceState({}, '', '/');
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    window.history.replaceState({}, '', '/');
  });

  it('keeps an explicit workspace instance off the global Bench stream even when internal mode is enabled', async () => {
    window.history.replaceState({}, '', '/?instance=scoped&workspace=%2Fworkspace%2Fscoped');
    const { rerender } = render(<App />);
    await waitFor(() => expect(runtimeSubscriptions().length).toBeGreaterThan(0));
    rerender(<App />);

    expect(runtimeSubscriptions().every((channels) => !channels.includes('event.bench'))).toBe(true);
    expect(runtimeSubscriptions().every((channels) => channels.includes('event.factory'))).toBe(true);
  });

  it('retains the global Bench stream for the explicitly authorized unpinned internal observer', async () => {
    window.history.replaceState({}, '', '/?globalObserver=1');
    render(<App />);
    await waitFor(() => expect(runtimeSubscriptions()).toEqual([
      expect.arrayContaining(['event.factory', 'event.bench']),
    ]));
    expect(harness.factoryBenchOptions).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: true, autoSelect: 'newest' }));
  });
});


describe('App explicit global Bench opt-in boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv('DEV', true);
    vi.stubEnv('VITE_POLARIS_INTERNAL_BENCH', '1');
    vi.stubEnv('VITE_POLARIS_GLOBAL_BENCH_OBSERVER', '');
    vi.stubEnv('VITE_POLARIS_WORKSPACE', '');
    vi.stubEnv('VITE_WORKSPACE', '');
    vi.stubEnv('VITE_POLARIS_INSTANCE_ID', '');
    window.history.replaceState({}, '', '/');
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    window.history.replaceState({}, '', '/');
  });

  it.each([
    ['internal availability alone', '/', {}, false],
    ['DEV default alone', '/', { VITE_POLARIS_INTERNAL_BENCH: '' }, false],
    ['false URL flag', '/?globalObserver=0', {}, false],
    ['unknown URL flag', '/?globalObserver=true', {}, false],
    ['false environment flag', '/', { VITE_POLARIS_GLOBAL_BENCH_OBSERVER: '0' }, false],
    ['unknown environment flag', '/', { VITE_POLARIS_GLOBAL_BENCH_OBSERVER: 'true' }, false],
    ['explicit environment opt-in', '/', { VITE_POLARIS_GLOBAL_BENCH_OBSERVER: '1' }, true],
    ['URL refusal overrides environment opt-in', '/?globalObserver=0', { VITE_POLARIS_GLOBAL_BENCH_OBSERVER: '1' }, false],
    ['URL workspace pin', '/?globalObserver=1&workspace=%2Fworkspace%2Fscoped', {}, false],
    ['URL workspace alias pin', '/?globalObserver=1&polarisWorkspace=%2Fworkspace%2Fscoped', {}, false],
    ['URL instance pin without workspace', '/?globalObserver=1&instance=scoped', {}, false],
    ['URL instanceId pin without workspace', '/?globalObserver=1&instanceId=scoped', {}, false],
    ['URL instance alias pin without workspace', '/?globalObserver=1&polarisInstance=scoped', {}, false],
    ['environment workspace pin', '/?globalObserver=1', { VITE_POLARIS_WORKSPACE: '/workspace/scoped' }, false],
    ['environment workspace alias pin', '/?globalObserver=1', { VITE_WORKSPACE: '/workspace/scoped' }, false],
    ['environment instance pin', '/?globalObserver=1', { VITE_POLARIS_INSTANCE_ID: 'scoped' }, false],
    ['disabled internal mode', '/?globalObserver=1', { VITE_POLARIS_INTERNAL_BENCH: '0' }, false],
    ['production internal mode unavailable', '/?globalObserver=1', { DEV: false, VITE_POLARIS_INTERNAL_BENCH: '' }, false],
  ] as const)('authorizes channels and Bench hook for %s', async (_label, url, environment, enabled) => {
    Object.entries(environment).forEach(([key, value]) => {
      if (key === 'DEV') vi.stubEnv('DEV', value as boolean);
      else vi.stubEnv(key, String(value));
    });
    window.history.replaceState({}, '', url);
    render(<App />);
    await waitFor(() => expect(runtimeSubscriptions().length).toBeGreaterThan(0));
    expect(runtimeSubscriptions().every((channels) => channels.includes('event.bench') === enabled)).toBe(true);
    expect(runtimeSubscriptions().every((channels) => channels.includes('event.factory'))).toBe(true);
    expect(harness.factoryBenchOptions).toHaveBeenLastCalledWith(expect.objectContaining({ enabled, autoSelect: enabled ? 'newest' : 'none' }));
  });
});


describe('App engineering-only production entry', () => {
  const snapshot = (status = 'running') => ({ session_id: 'engineering-1', run_id: 'engineering-1', engine_version: 'engineering_v2', workspace: '/workspace/scoped', workflow: 'direct', role: 'director', status, active_roles: ['director'], roles: { pm: 'not_called', chief_engineer: 'not_called', director: status, qa: 'not_called' }, text: '', verification: 'not_run', checks: [], required_checks: [], permission_profile_id: 'host-profile', resource_url: '/v2/engineering/runs/engineering-1', last_seq: 0, events: [] });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv('VITE_POLARIS_INTERNAL_BENCH', '0');
    vi.stubEnv('VITE_POLARIS_WORKSPACE', '');
    vi.stubEnv('VITE_WORKSPACE', '');
    vi.stubEnv('VITE_POLARIS_INSTANCE_ID', '');
    harness.start.mockResolvedValue({ ok: true, data: snapshot() });
    harness.get.mockResolvedValue({ ok: true, data: snapshot() });
    harness.cancel.mockResolvedValue({ ok: true, data: snapshot('cancelled') });
    harness.resume.mockResolvedValue({ ok: true, data: snapshot() });
    window.history.replaceState({}, '', '/?instance=scoped&backend=http%3A%2F%2F127.0.0.1%3A5555&token=bound-token&workspace=%2Fworkspace%2Fscoped');
  });
  afterEach(() => { cleanup(); vi.unstubAllEnvs(); window.history.replaceState({}, '', '/'); });
  it.each(['', '&engine=legacy'])('defaults to Director and creates only an engineering run from a clean URL (%s)', async (suffix) => {
    window.history.replaceState({}, '', window.location.href + suffix);
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Engineering v2 · Director' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Implement the requested change' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    await waitFor(() => expect(window.location.search).toContain('engineering_run=engineering-1'));
    expect(harness.start).toHaveBeenCalledWith(expect.objectContaining({ workspace: '/workspace/scoped', instruction: 'Implement the requested change', role: 'director', workflow: 'direct', required_checks: [] }));
    expect(harness.factoryOptions).not.toHaveBeenCalled();
    const params = new URLSearchParams(window.location.search);
    expect(params.get('backend')).toBe('http://127.0.0.1:5555');
    expect(params.get('token')).toBe('bound-token');
    expect(params.get('instance')).toBe('scoped');
  });
  it('preserves the same engineering run across context, role navigation, cancellation, resume and reload', async () => {
    const app = render(<App />);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Build feature' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    await waitFor(() => expect(window.location.search).toContain('engineering_run=engineering-1'));
    fireEvent.click(screen.getByRole('button', { name: 'ContextOS' }));
    fireEvent.click(screen.getByRole('button', { name: '返回工程运行' }));
    fireEvent.click(screen.getByRole('button', { name: 'PM 规划' }));
    expect(screen.getByRole('heading', { name: 'Engineering v2 · PM' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Director 工程' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '继续同一运行' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '继续同一运行' }));
    await waitFor(() => expect(harness.resume).toHaveBeenCalledWith('engineering-1', '/workspace/scoped'));
    expect(harness.cancel).toHaveBeenCalledWith('engineering-1', '/workspace/scoped');
    app.unmount(); render(<App />);
    await waitFor(() => expect(harness.get).toHaveBeenCalledWith('engineering-1', '/workspace/scoped'));
    expect(harness.start).toHaveBeenCalledTimes(1);
    expect(window.location.search).toContain('engineering_run=engineering-1');
    expect(harness.factoryOptions).not.toHaveBeenCalled();
  });
  it('pins a settings-derived workspace into the run URL and ignores later settings drift', async () => {
    window.history.replaceState({}, '', '/');
    harness.settings.workspace = '/workspace/main';
    harness.start.mockResolvedValue({ ok: true, data: { ...snapshot(), workspace: '/workspace/main' } });
    harness.get.mockResolvedValue({ ok: true, data: { ...snapshot(), workspace: '/workspace/main' } });
    const app = render(<App />);
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Pinned work' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('workspace')).toBe('/workspace/main'));
    harness.settings.workspace = '/workspace/other';
    app.rerender(<App />);
    expect(screen.getByTitle('/workspace/main')).toBeInTheDocument();
    expect(harness.start).toHaveBeenCalledWith(expect.objectContaining({ workspace: '/workspace/main' }));
    harness.settings.workspace = '/workspace/main';
  });
  it('blocks an instance with no workspace binding instead of writing to settings workspace', () => {
    window.history.replaceState({}, '', '/?instance=missing-workspace');
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent('实例缺少 workspace 绑定');
    expect(screen.queryByRole('button', { name: '开始执行' })).not.toBeInTheDocument();
    expect(harness.start).not.toHaveBeenCalled();
  });
});
