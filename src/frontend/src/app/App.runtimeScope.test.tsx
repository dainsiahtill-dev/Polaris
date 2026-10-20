import React from 'react';
import { cleanup, render, waitFor } from '@testing-library/react';
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
vi.mock('@/hooks/useFactory', () => ({ useFactory: () => ({ currentRun: null }) }));
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
