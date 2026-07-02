import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, GitCompare, Loader2, RefreshCw, Settings2, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type { BackendSettings } from '@/app/types/appContracts';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import { settingsService } from '@/services';
import { StrategyDiffViewer, type StrategyVersion } from './StrategyDiffViewer';
import { StrategyEditorPanel, type DirectorExecutionStrategy } from './StrategyEditorPanel';

type StrategySaveState = 'idle' | 'saving' | 'saved' | 'error';
type StrategyPanelView = 'editor' | 'diff';

interface DirectorStrategyPanelProps {
  workspace: string;
  tasksCount?: number;
  runningTasks?: number;
}

const DIRECTOR_STRATEGY_DEFAULTS: DirectorExecutionStrategy = {
  name: 'director-runtime',
  version: '1.0.0',
  mode: 'parallel',
  limits: {
    iterations: 1,
    maxParallelTasks: 3,
    readyTimeoutSeconds: 30,
    claimTimeoutSeconds: 30,
    phaseTimeoutSeconds: 900,
    completeTimeoutSeconds: 30,
    taskTimeoutSeconds: 3600,
  },
  observability: {
    forever: false,
    showOutput: true,
  },
  metadata: {
    source: 'polaris-settings',
  },
};

export function buildDirectorStrategyFromSettings(
  settings: Partial<BackendSettings> | null | undefined,
  workspace: string,
): DirectorExecutionStrategy {
  const mode = normalizeMode(settings?.director_execution_mode);
  return {
    ...DIRECTOR_STRATEGY_DEFAULTS,
    mode,
    limits: {
      iterations: readPositiveInteger(settings?.director_iterations, DIRECTOR_STRATEGY_DEFAULTS.limits.iterations),
      maxParallelTasks: readPositiveInteger(
        settings?.director_max_parallel_tasks,
        DIRECTOR_STRATEGY_DEFAULTS.limits.maxParallelTasks,
      ),
      readyTimeoutSeconds: readPositiveInteger(
        settings?.director_ready_timeout_seconds,
        DIRECTOR_STRATEGY_DEFAULTS.limits.readyTimeoutSeconds,
      ),
      claimTimeoutSeconds: readPositiveInteger(
        settings?.director_claim_timeout_seconds,
        DIRECTOR_STRATEGY_DEFAULTS.limits.claimTimeoutSeconds,
      ),
      phaseTimeoutSeconds: readPositiveInteger(
        settings?.director_phase_timeout_seconds,
        DIRECTOR_STRATEGY_DEFAULTS.limits.phaseTimeoutSeconds,
      ),
      completeTimeoutSeconds: readPositiveInteger(
        settings?.director_complete_timeout_seconds,
        DIRECTOR_STRATEGY_DEFAULTS.limits.completeTimeoutSeconds,
      ),
      taskTimeoutSeconds: readPositiveInteger(
        settings?.director_task_timeout_seconds,
        DIRECTOR_STRATEGY_DEFAULTS.limits.taskTimeoutSeconds,
      ),
    },
    observability: {
      forever: Boolean(settings?.director_forever ?? DIRECTOR_STRATEGY_DEFAULTS.observability.forever),
      showOutput: Boolean(settings?.director_show_output ?? DIRECTOR_STRATEGY_DEFAULTS.observability.showOutput),
    },
    metadata: {
      source: '/v2/settings',
      workspace: settings?.workspace || workspace,
      updatedAt: new Date().toISOString(),
    },
  };
}

export function buildDirectorSettingsUpdateFromStrategy(
  strategy: DirectorExecutionStrategy,
): Partial<BackendSettings> {
  return {
    director_execution_mode: normalizeMode(strategy.mode),
    director_iterations: readPositiveInteger(strategy.limits.iterations, DIRECTOR_STRATEGY_DEFAULTS.limits.iterations),
    director_max_parallel_tasks: readPositiveInteger(
      strategy.limits.maxParallelTasks,
      DIRECTOR_STRATEGY_DEFAULTS.limits.maxParallelTasks,
    ),
    director_ready_timeout_seconds: readPositiveInteger(
      strategy.limits.readyTimeoutSeconds,
      DIRECTOR_STRATEGY_DEFAULTS.limits.readyTimeoutSeconds,
    ),
    director_claim_timeout_seconds: readPositiveInteger(
      strategy.limits.claimTimeoutSeconds,
      DIRECTOR_STRATEGY_DEFAULTS.limits.claimTimeoutSeconds,
    ),
    director_phase_timeout_seconds: readPositiveInteger(
      strategy.limits.phaseTimeoutSeconds,
      DIRECTOR_STRATEGY_DEFAULTS.limits.phaseTimeoutSeconds,
    ),
    director_complete_timeout_seconds: readPositiveInteger(
      strategy.limits.completeTimeoutSeconds,
      DIRECTOR_STRATEGY_DEFAULTS.limits.completeTimeoutSeconds,
    ),
    director_task_timeout_seconds: readPositiveInteger(
      strategy.limits.taskTimeoutSeconds,
      DIRECTOR_STRATEGY_DEFAULTS.limits.taskTimeoutSeconds,
    ),
    director_forever: Boolean(strategy.observability.forever),
    director_show_output: Boolean(strategy.observability.showOutput),
  };
}

function normalizeMode(value: unknown): 'serial' | 'parallel' {
  return String(value || '').trim().toLowerCase() === 'serial' ? 'serial' : 'parallel';
}

function readPositiveInteger(value: unknown, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.floor(parsed));
}

function formatStrategy(strategy: DirectorExecutionStrategy): string {
  return JSON.stringify(strategy, null, 2);
}

function createStrategyVersion(
  content: string,
  message: string,
  timestamp = new Date().toISOString(),
): StrategyVersion {
  const parsed = parseStrategyContent(content);
  return {
    id: `${timestamp}-${message}`,
    version: parsed?.version || '1.0.0',
    content,
    timestamp,
    author: 'Polaris Desktop',
    message,
  };
}

function parseStrategyContent(content: string): DirectorExecutionStrategy | null {
  try {
    return JSON.parse(content) as DirectorExecutionStrategy;
  } catch {
    return null;
  }
}

export function DirectorStrategyPanel({
  workspace,
  tasksCount = 0,
  runningTasks = 0,
}: DirectorStrategyPanelProps) {
  const [activeView, setActiveView] = useState<StrategyPanelView>('editor');
  const [settingsSnapshot, setSettingsSnapshot] = useState<BackendSettings | null>(null);
  const [strategyJson, setStrategyJson] = useState(() => formatStrategy(buildDirectorStrategyFromSettings(null, workspace)));
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [saveState, setSaveState] = useState<StrategySaveState>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentStrategy = useMemo(
    () => buildDirectorStrategyFromSettings(settingsSnapshot, workspace),
    [settingsSnapshot, workspace],
  );
  const displayWorkspace = workspaceLabel(workspace, '');

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await settingsService.get();
      if (!result.ok || !result.data) {
        const detail = result.error || 'Director settings unavailable';
        setError(detail);
        setSaveState('error');
        setMessage(detail);
        return;
      }
      const strategy = buildDirectorStrategyFromSettings(result.data, workspace);
      const nextJson = formatStrategy(strategy);
      setSettingsSnapshot(result.data);
      setStrategyJson(nextJson);
      setVersions((prev) => {
        if (prev.some((item) => item.content === nextJson)) return prev;
        return [createStrategyVersion(nextJson, 'loaded'), ...prev].slice(0, 6);
      });
      setSaveState('idle');
      setMessage('已读取 /settings');
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Director settings unavailable';
      setError(detail);
      setSaveState('error');
      setMessage(detail);
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const handleSaveStrategy = useCallback(async (strategy: DirectorExecutionStrategy) => {
    const previousJson = strategyJson;
    const updatePayload = buildDirectorSettingsUpdateFromStrategy(strategy);
    setSaveState('saving');
    setMessage('正在同步到 /settings');
    setError(null);

    const result = await settingsService.update(updatePayload);
    if (!result.ok || !result.data) {
      const detail = result.error || 'Failed to update Director settings';
      setSaveState('error');
      setMessage(detail);
      setError(detail);
      throw new Error(detail);
    }

    const canonicalStrategy = {
      ...buildDirectorStrategyFromSettings(result.data, workspace),
      name: strategy.name,
      version: strategy.version,
    };
    const nextJson = formatStrategy(canonicalStrategy);
    setSettingsSnapshot(result.data);
    setStrategyJson(nextJson);
    setVersions((prev) => {
      const timeline = [
        createStrategyVersion(previousJson, 'before-save'),
        createStrategyVersion(nextJson, 'after-save'),
        ...prev,
      ];
      const deduped = timeline.filter((item, index, all) => (
        all.findIndex((candidate) => candidate.content === item.content) === index
      ));
      return deduped.slice(0, 6);
    });
    setSaveState('saved');
    setMessage('已同步到 /settings');
  }, [strategyJson, workspace]);

  const statusLabel = loading
    ? 'loading'
    : error
      ? 'error'
      : saveState === 'saved'
        ? 'synced'
        : 'ready';

  return (
    <section
      className="flex h-full flex-col overflow-hidden bg-[linear-gradient(165deg,rgba(15,23,42,0.98),rgba(30,27,75,0.70),rgba(8,15,31,0.98))]"
      data-testid="director-strategy-panel"
    >
      <header className="flex min-h-16 items-center justify-between gap-4 border-b border-indigo-400/[0.15] px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyan-400/25 bg-cyan-500/10">
            <SlidersHorizontal className="h-4 w-4 text-cyan-200" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-slate-100">Director 策略控制</h2>
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-[10px] text-slate-400">
              <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-cyan-200">/settings</span>
              <span
                data-testid="director-strategy-workspace-label"
                className="max-w-[220px] truncate rounded border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono"
                title={workspace}
                data-workspace-path={workspace}
              >
                workspace={displayWorkspace}
              </span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <MetricPill label="mode" value={currentStrategy.mode} tone={currentStrategy.mode === 'parallel' ? 'cyan' : 'slate'} />
          <MetricPill label="tasks" value={`${runningTasks}/${tasksCount}`} tone={runningTasks > 0 ? 'emerald' : 'slate'} />
          <div
            className={cn(
              'flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]',
              error
                ? 'border-red-500/25 bg-red-500/10 text-red-200'
                : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
            )}
            data-testid="director-strategy-status"
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : error ? (
              <AlertTriangle className="h-3 w-3" />
            ) : (
              <CheckCircle2 className="h-3 w-3" />
            )}
            {statusLabel}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => { void loadSettings(); }}
            disabled={loading || saveState === 'saving'}
            title="刷新 Director 策略设置"
            className="h-8 w-8 text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-200"
          >
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          </Button>
        </div>
      </header>

      <div className="flex min-h-11 items-center justify-between gap-3 border-b border-white/10 bg-slate-950/40 px-4 py-2">
        <div className="flex items-center gap-2">
          <ViewTab
            active={activeView === 'editor'}
            icon={<Settings2 className="h-3.5 w-3.5" />}
            label="编辑"
            onClick={() => setActiveView('editor')}
          />
          <ViewTab
            active={activeView === 'diff'}
            icon={<GitCompare className="h-3.5 w-3.5" />}
            label="对比"
            onClick={() => setActiveView('diff')}
          />
        </div>
        <div className="min-w-0 truncate text-[11px] text-slate-400" data-testid="director-strategy-message">
          {message || '策略保存后会立即写入后端运行设置'}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeView === 'editor' ? (
          <StrategyEditorPanel
            initialStrategy={strategyJson}
            onSave={handleSaveStrategy}
            saveState={saveState}
            saveMessage={message}
            saveButtonLabel="应用"
          />
        ) : (
          <StrategyDiffViewer versions={versions} splitView />
        )}
      </div>
    </section>
  );
}

function MetricPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone: 'cyan' | 'emerald' | 'slate';
}) {
  const toneClass = tone === 'cyan'
    ? 'border-cyan-400/25 bg-cyan-500/10 text-cyan-200'
    : tone === 'emerald'
      ? 'border-emerald-400/25 bg-emerald-500/10 text-emerald-200'
      : 'border-white/10 bg-white/5 text-slate-300';
  return (
    <div className={cn('flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]', toneClass)}>
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

function ViewTab({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex h-7 items-center gap-1.5 rounded border px-2 text-xs transition-colors',
        active
          ? 'border-indigo-400/30 bg-indigo-500/[0.15] text-indigo-100'
          : 'border-white/10 bg-white/[0.03] text-slate-400 hover:bg-white/5 hover:text-slate-200',
      )}
    >
      {icon}
      {label}
    </button>
  );
}
