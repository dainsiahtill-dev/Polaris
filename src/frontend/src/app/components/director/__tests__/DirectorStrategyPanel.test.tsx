import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { BackendSettings } from '@/app/types/appContracts';
import {
  DirectorStrategyPanel,
  buildDirectorSettingsUpdateFromStrategy,
  buildDirectorStrategyFromSettings,
} from '../DirectorStrategyPanel';
import type { DirectorExecutionStrategy } from '../StrategyEditorPanel';

const settingsServiceMock = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
}));

vi.mock('@/services', () => ({
  settingsService: settingsServiceMock,
}));

vi.mock('../StrategyEditorPanel', () => ({
  StrategyEditorPanel: ({
    initialStrategy,
    onSave,
    saveState,
    saveMessage,
  }: {
    initialStrategy?: string;
    onSave?: (strategy: DirectorExecutionStrategy) => void | Promise<void>;
    saveState?: string;
    saveMessage?: string | null;
  }) => (
    <div data-testid="strategy-editor-panel-mock" data-save-state={saveState || ''}>
      <pre data-testid="strategy-editor-json">{initialStrategy || ''}</pre>
      <span data-testid="strategy-editor-message">{saveMessage || ''}</span>
      <button
        type="button"
        onClick={() => {
          void onSave?.({
            name: 'director-serial-test',
            version: '1.2.3',
            mode: 'serial',
            limits: {
              iterations: 4,
              maxParallelTasks: 2,
              readyTimeoutSeconds: 45,
              claimTimeoutSeconds: 40,
              phaseTimeoutSeconds: 800,
              completeTimeoutSeconds: 35,
              taskTimeoutSeconds: 2400,
            },
            observability: {
              forever: true,
              showOutput: false,
            },
          });
        }}
      >
        save-strategy
      </button>
    </div>
  ),
}));

vi.mock('../StrategyDiffViewer', () => ({
  StrategyDiffViewer: ({ versions }: { versions?: unknown[] }) => (
    <div data-testid="strategy-diff-viewer-mock">versions={versions?.length ?? 0}</div>
  ),
}));

const baseSettings: BackendSettings = {
  workspace: 'C:/Temp/Product',
  pm_backend: 'auto',
  model: 'qwen-test',
  prompt_profile: 'default',
  interval: 20,
  timeout: 0,
  refresh_interval: 3,
  auto_refresh: true,
  show_memory: false,
  director_execution_mode: 'parallel',
  director_iterations: 2,
  director_max_parallel_tasks: 3,
  director_ready_timeout_seconds: 30,
  director_claim_timeout_seconds: 30,
  director_phase_timeout_seconds: 900,
  director_complete_timeout_seconds: 30,
  director_task_timeout_seconds: 3600,
  director_forever: false,
  director_show_output: true,
};

describe('DirectorStrategyPanel settings bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    settingsServiceMock.get.mockResolvedValue({
      ok: true,
      data: baseSettings,
    });
    settingsServiceMock.update.mockResolvedValue({
      ok: true,
      data: {
        ...baseSettings,
        director_execution_mode: 'serial',
        director_iterations: 4,
        director_max_parallel_tasks: 2,
        director_ready_timeout_seconds: 45,
        director_claim_timeout_seconds: 40,
        director_phase_timeout_seconds: 800,
        director_complete_timeout_seconds: 35,
        director_task_timeout_seconds: 2400,
        director_forever: true,
        director_show_output: false,
      },
    });
  });

  it('maps backend settings into a Director execution strategy', () => {
    const strategy = buildDirectorStrategyFromSettings(baseSettings, 'C:/Fallback');

    expect(strategy.mode).toBe('parallel');
    expect(strategy.limits.iterations).toBe(2);
    expect(strategy.limits.maxParallelTasks).toBe(3);
    expect(strategy.observability.showOutput).toBe(true);
    expect(strategy.metadata?.workspace).toBe('C:/Temp/Product');
  });

  it('maps a Director execution strategy back to backend settings updates', () => {
    const update = buildDirectorSettingsUpdateFromStrategy({
      name: 'director-test',
      version: '1.0.0',
      mode: 'serial',
      limits: {
        iterations: 5,
        maxParallelTasks: 1,
        readyTimeoutSeconds: 20,
        claimTimeoutSeconds: 21,
        phaseTimeoutSeconds: 600,
        completeTimeoutSeconds: 22,
        taskTimeoutSeconds: 1800,
      },
      observability: {
        forever: false,
        showOutput: false,
      },
    });

    expect(update).toEqual({
      director_execution_mode: 'serial',
      director_iterations: 5,
      director_max_parallel_tasks: 1,
      director_ready_timeout_seconds: 20,
      director_claim_timeout_seconds: 21,
      director_phase_timeout_seconds: 600,
      director_complete_timeout_seconds: 22,
      director_task_timeout_seconds: 1800,
      director_forever: false,
      director_show_output: false,
    });
  });

  it('loads Director settings and saves editor changes through the backend settings route', async () => {
    render(
      <DirectorStrategyPanel
        workspace="C:/Temp/Product"
        tasksCount={4}
        runningTasks={1}
      />,
    );

    const editorJson = await screen.findByTestId('strategy-editor-json');
    expect(settingsServiceMock.get).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(editorJson).toHaveTextContent('"mode": "parallel"');
      expect(editorJson).toHaveTextContent('"iterations": 2');
    });
    expect(screen.getByTestId('director-strategy-message')).toHaveTextContent('已读取 /settings');
    expect(screen.getByTestId('director-strategy-panel')).toHaveTextContent('/settings');
    expect(screen.getByTestId('director-strategy-workspace-label')).toHaveTextContent('workspace=Product');
    expect(screen.getByTestId('director-strategy-workspace-label')).not.toHaveTextContent('C:/Temp');
    expect(screen.getByTestId('director-strategy-workspace-label')).toHaveAttribute('title', 'C:/Temp/Product');
    expect(screen.getByTestId('director-strategy-workspace-label')).toHaveAttribute(
      'data-workspace-path',
      'C:/Temp/Product',
    );
    expect(screen.getByTestId('director-strategy-panel')).toHaveTextContent('tasks1/4');

    fireEvent.click(screen.getByText('save-strategy'));

    await waitFor(() => {
      expect(settingsServiceMock.update).toHaveBeenCalledWith({
        director_execution_mode: 'serial',
        director_iterations: 4,
        director_max_parallel_tasks: 2,
        director_ready_timeout_seconds: 45,
        director_claim_timeout_seconds: 40,
        director_phase_timeout_seconds: 800,
        director_complete_timeout_seconds: 35,
        director_task_timeout_seconds: 2400,
        director_forever: true,
        director_show_output: false,
      });
    });
    expect(await screen.findByTestId('director-strategy-message')).toHaveTextContent('已同步到 /settings');

    fireEvent.click(screen.getByText('对比'));
    expect(await screen.findByTestId('strategy-diff-viewer-mock')).toHaveTextContent('versions=2');
  });
});
