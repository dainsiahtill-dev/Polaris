import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ExternalLink,
  Info,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Server,
  Square,
  TerminalSquare,
  Trash2,
  X,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import {
  useConnectionState,
  useMessageHandler,
  useTransportActions,
} from '@/runtime/transport';
import {
  buildInstanceWorkspaceUrl,
  deleteInstance,
  getInstanceLogs,
  listInstances,
  restartInstance,
  startInstance,
  stopInstance,
  type PolarisInstance,
  type StartInstancePayload,
} from '@/services/instances';

type LogSelection = {
  instanceId: string;
  stream: 'backend' | 'frontend';
  content: string;
};

export function isLauncherBackendReady(instance: PolarisInstance): boolean {
  return String(instance.metadata?.backend_health || '').trim() === 'ok';
}

export function isLauncherBackendOpenable(instance: PolarisInstance): boolean {
  const backendHealth = String(instance.metadata?.backend_health || '').trim();
  const backendOpenable =
    isLauncherBackendReady(instance) ||
    (instance.status === 'running' && (backendHealth === 'process' || Boolean(instance.backend_alive)));
  if (!backendOpenable) return false;
  if (instance.start_frontend === false) return true;
  const frontendHealth = String(instance.metadata?.frontend_health || '').trim();
  return frontendHealth === 'ok' || frontendHealth === 'process' || Boolean(instance.frontend_alive);
}

export function launcherInstanceStatusTone(instance: PolarisInstance): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (instance.status === 'running' && isLauncherBackendOpenable(instance)) return 'success';
  if (instance.status === 'running') return 'warning';
  if (instance.status === 'observed') return 'info';
  if (instance.status === 'failed' || instance.status === 'error') return 'error';
  if (instance.backend_pid || instance.frontend_pid) return 'warning';
  return 'default';
}

export function isLauncherInstanceStoppable(instance: PolarisInstance): boolean {
  if (instance.status === 'stopped') return false;
  if (instance.status === 'running' || instance.status === 'observed') return true;
  return Boolean(instance.backend_alive || instance.frontend_alive || instance.backend_pid || instance.frontend_pid);
}

function usesSharedBackendBinding(instance: PolarisInstance): boolean {
  return String(instance.metadata?.backend_binding || '') === 'shared_backend_workspace_switch';
}

function restartActionLabel(instance: PolarisInstance): string {
  return usesSharedBackendBinding(instance) ? '独立启动' : '重启';
}

function isStoppedInternalBench(instance: PolarisInstance): boolean {
  return (
    instance.kind === 'bench_project' &&
    instance.status !== 'running' &&
    !instance.backend_alive &&
    Boolean(instance.metadata?.internal_test_only)
  );
}

function currentControlInstanceId(): string {
  if (typeof window !== 'undefined') {
    const raw = new URLSearchParams(window.location.search).get('instance');
    if (raw && raw.trim()) return raw.trim();
  }
  const envInstanceId = import.meta.env.VITE_POLARIS_INSTANCE_ID;
  if (typeof envInstanceId === 'string' && envInstanceId.trim()) return envInstanceId.trim();
  return 'main';
}

export function isCurrentControlInstance(instance: PolarisInstance, currentInstanceId = currentControlInstanceId()): boolean {
  return Boolean(currentInstanceId) && instance.instance_id === currentInstanceId;
}

function basename(path: string): string {
  const normalized = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return normalized.split('/').filter(Boolean).pop() || normalized || 'workspace';
}

function stringField(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function timestampEpoch(value: unknown): number {
  if (typeof value !== 'string' || !value.trim()) return 0;
  const epoch = Date.parse(value);
  return Number.isFinite(epoch) ? epoch : 0;
}

export function launcherInstanceRecencyEpoch(instance: PolarisInstance): number {
  return (
    timestampEpoch(instance.created_at) ||
    timestampEpoch(instance.last_started_at) ||
    timestampEpoch(instance.updated_at) ||
    timestampEpoch(instance.last_stopped_at)
  );
}

export function sortLauncherInstancesByNewest(instances: PolarisInstance[]): PolarisInstance[] {
  return [...instances].sort((left, right) => {
    const timeDelta = launcherInstanceRecencyEpoch(right) - launcherInstanceRecencyEpoch(left);
    if (timeDelta !== 0) return timeDelta;
    return right.instance_id.localeCompare(left.instance_id);
  });
}

export function instanceSubtitle(instance: PolarisInstance): string {
  const parts = [instance.instance_id, instance.kind].filter(Boolean);
  if (instance.kind === 'bench_project') {
    const projectId = stringField(instance.bench?.project_id);
    const benchWorkspace = stringField(instance.bench?.bench_workspace);
    if (projectId && !parts.includes(projectId)) parts.push(projectId);
    if (benchWorkspace) parts.push(basename(benchWorkspace));
  }
  return parts.join(' · ');
}

function openInstance(instance: PolarisInstance): void {
  window.open(buildInstanceWorkspaceUrl(instance), '_blank', 'noopener,noreferrer');
}

function formatJson(value: Record<string, unknown>): string {
  const entries = Object.keys(value || {});
  return entries.length > 0 ? JSON.stringify(value, null, 2) : '{}';
}

const defaultForm: StartInstancePayload = {
  kind: 'project',
  workspace: '',
  name: '',
  backend_reload: false,
  frontend_vite: true,
  start_frontend: true,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isInstanceStatusMessage(message: unknown): boolean {
  if (!isRecord(message)) return false;
  if (String(message.channel || '').trim() === 'status.instances') return true;
  if (
    message.type === 'EVENT' &&
    message.protocol === 'runtime.v2' &&
    isRecord(message.event) &&
    String(message.event.channel || '').trim() === 'status.instances'
  ) {
    return true;
  }
  return false;
}

export function LauncherWorkspace() {
  const [instances, setInstances] = useState<PolarisInstance[]>([]);
  const [form, setForm] = useState<StartInstancePayload>(defaultForm);
  const [loading, setLoading] = useState(false);
  const [actionId, setActionId] = useState('');
  const [error, setError] = useState('');
  const [logs, setLogs] = useState<LogSelection | null>(null);
  const [selectedInstanceId, setSelectedInstanceId] = useState('');
  const { subscribeChannels } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();
  const connection = useConnectionState();

  const runningCount = useMemo(
    () => instances.filter((item) => item.status === 'running' && isLauncherBackendOpenable(item)).length,
    [instances],
  );
  const benchCount = useMemo(
    () => instances.filter((item) => item.kind === 'bench_project').length,
    [instances],
  );
  const stoppedBenchCount = useMemo(
    () => instances.filter(isStoppedInternalBench).length,
    [instances],
  );
  const orderedInstances = useMemo(
    () => sortLauncherInstancesByNewest(instances),
    [instances],
  );
  const selectedInstance = useMemo(
    () => instances.find((item) => item.instance_id === selectedInstanceId) || null,
    [instances, selectedInstanceId],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    const result = await listInstances();
    if (result.ok && result.data) {
      setInstances(result.data.instances);
    } else {
      setError(result.error || '实例列表读取失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const unsubscribe = subscribeChannels([{ channel: 'status.instances', tailLines: 0 }]);
    const unregister = registerMessageHandler((message) => {
      if (isInstanceStatusMessage(message)) {
        void refresh();
      }
    });
    return () => {
      unregister();
      unsubscribe();
    };
  }, [refresh, registerMessageHandler, subscribeChannels]);

  const submitStart = useCallback(async () => {
    if (!form.workspace?.trim()) {
      setError('workspace 不能为空');
      return;
    }
    setActionId('start');
    setError('');
    const result = await startInstance({
      ...form,
      name: form.name?.trim() || basename(form.workspace),
    });
    if (!result.ok) {
      setError(result.error || '启动失败');
    }
    await refresh();
    setActionId('');
  }, [form, refresh]);

  const runAction = useCallback(async (
    instance: PolarisInstance,
    action: 'stop' | 'restart' | 'delete' | 'backend-logs' | 'frontend-logs',
  ) => {
    setActionId(`${action}:${instance.instance_id}`);
    setError('');
    if (action === 'stop') {
      const result = await stopInstance(instance.instance_id);
      if (!result.ok) setError(result.error || '停止失败');
      await refresh();
    } else if (action === 'restart') {
      const result = await restartInstance(instance.instance_id);
      if (!result.ok) setError(result.error || '重启失败');
      await refresh();
    } else if (action === 'delete') {
      const result = await deleteInstance(instance.instance_id);
      if (!result.ok) setError(result.error || '删除失败');
      await refresh();
    } else {
      const stream = action === 'frontend-logs' ? 'frontend' : 'backend';
      const result = await getInstanceLogs(instance.instance_id, stream);
      if (result.ok && result.data) {
        setLogs({ instanceId: instance.instance_id, stream, content: result.data.content });
      } else {
        setError(result.error || '日志读取失败');
      }
    }
    setActionId('');
  }, [refresh]);

  const cleanupStoppedBench = useCallback(async () => {
    const targets = instances.filter(isStoppedInternalBench);
    if (targets.length === 0) return;
    setActionId('cleanup-stopped-bench');
    setError('');
    const failures: string[] = [];
    for (const instance of targets) {
      const result = await deleteInstance(instance.instance_id);
      if (!result.ok) failures.push(instance.instance_id);
    }
    if (failures.length > 0) {
      setError(`清理失败: ${failures.join(', ')}`);
    }
    await refresh();
    setActionId('');
  }, [instances, refresh]);

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-slate-950 text-slate-100">
      <header className="shrink-0 border-b border-cyan-400/20 bg-slate-950/95 px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-400/10 text-cyan-200">
              <Server className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Polaris Launcher</h1>
              <p className="text-xs text-slate-400">多实例总控 · 每个实例保持唯一 workspace</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge color="success" variant="dot">{runningCount} running</StatusBadge>
            <StatusBadge color="info" variant="dot">{instances.length} instances</StatusBadge>
            <StatusBadge color="warning" variant="dot">{benchCount} bench</StatusBadge>
            <StatusBadge color={connection.connected ? 'success' : connection.reconnecting ? 'warning' : 'default'} variant="dot" pulse={connection.reconnecting}>
              {connection.connected ? 'WS live' : connection.reconnecting ? 'WS reconnect' : 'WS idle'}
            </StatusBadge>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              刷新
            </Button>
            {stoppedBenchCount > 0 ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void cleanupStoppedBench()}
                disabled={Boolean(actionId)}
              >
                <Trash2 className="h-4 w-4" />
                清理停止测试({stoppedBenchCount})
              </Button>
            ) : null}
          </div>
        </div>
      </header>

      <main
        className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto p-4 xl:grid-cols-[360px_minmax(0,1fr)]"
        data-testid="launcher-scroll-root"
      >
        <section className="h-fit rounded-lg border border-white/10 bg-white/[0.035] p-4 xl:sticky xl:top-0">
          <h2 className="text-sm font-semibold text-cyan-100">启动实例</h2>
          <div className="mt-4 space-y-3">
            <label className="block">
              <span className="text-[11px] uppercase text-slate-500">workspace</span>
              <input
                value={form.workspace || ''}
                onChange={(event) => setForm((prev) => ({ ...prev, workspace: event.target.value }))}
                placeholder="/path/to/project"
                className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <label className="block">
              <span className="text-[11px] uppercase text-slate-500">name</span>
              <input
                value={form.name || ''}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                placeholder="默认使用 workspace 名称"
                className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-[11px] uppercase text-slate-500">backend port</span>
                <input
                  value={form.backend_port ?? ''}
                  onChange={(event) => setForm((prev) => ({ ...prev, backend_port: event.target.value ? Number(event.target.value) : null }))}
                  placeholder="auto"
                  className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
                />
              </label>
              <label className="block">
                <span className="text-[11px] uppercase text-slate-500">frontend port</span>
                <input
                  value={form.frontend_port ?? ''}
                  onChange={(event) => setForm((prev) => ({ ...prev, frontend_port: event.target.value ? Number(event.target.value) : null }))}
                  placeholder="auto"
                  className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
                />
              </label>
            </div>
            <label className="block">
              <span className="text-[11px] uppercase text-slate-500">kind</span>
              <select
                value={form.kind || 'project'}
                onChange={(event) => setForm((prev) => ({ ...prev, kind: event.target.value }))}
                className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              >
                <option value="project">project</option>
                <option value="bench_project">bench_project</option>
                <option value="internal_test">internal_test</option>
              </select>
            </label>
            <div className="space-y-2 rounded-md border border-white/10 bg-slate-950/60 p-3">
              <label className="flex items-center justify-between gap-3 text-sm text-slate-300">
                backend --reload
                <input
                  type="checkbox"
                  checked={form.backend_reload !== false}
                  onChange={(event) => setForm((prev) => ({ ...prev, backend_reload: event.target.checked }))}
                />
              </label>
              <label className="flex items-center justify-between gap-3 text-sm text-slate-300">
                frontend Vite
                <input
                  type="checkbox"
                  checked={form.start_frontend !== false}
                  onChange={(event) => setForm((prev) => ({ ...prev, start_frontend: event.target.checked, frontend_vite: event.target.checked }))}
                />
              </label>
            </div>
            <Button className="w-full" onClick={() => void submitStart()} disabled={Boolean(actionId)}>
              {actionId === 'start' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              启动
            </Button>
          </div>
          {error ? (
            <div className="mt-4 rounded-md border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              {error}
            </div>
          ) : null}
        </section>

        <section
          className="flex min-h-[520px] min-w-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]"
          data-testid="launcher-instance-panel"
        >
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-100">实例</h2>
            <span className="text-xs text-slate-500">共 {instances.length} 个</span>
          </div>
          <div
            className="min-h-0 flex-1 overflow-y-auto p-4"
            data-testid="launcher-instance-list"
          >
            <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {instances.length === 0 ? (
              <div className="col-span-full rounded-lg border border-dashed border-white/10 p-8 text-center text-sm text-slate-500">
                暂无实例
              </div>
            ) : orderedInstances.map((instance) => {
              const isCurrentControl = isCurrentControlInstance(instance);
              const stoppingActionId = `stop:${instance.instance_id}`;
              const restartingActionId = `restart:${instance.instance_id}`;
              const deletingActionId = `delete:${instance.instance_id}`;
              const isStopping = actionId === stoppingActionId;
              const isRestarting = actionId === restartingActionId;
              const isDeleting = actionId === deletingActionId;
              const canStop = isLauncherInstanceStoppable(instance);
              const stopDisabled = Boolean(actionId) || isCurrentControl || !canStop;
              const stopTitle = isCurrentControl
                ? '当前控制后端不能自我停止'
                : canStop
                  ? '停止该 Polaris 实例'
                  : '实例已停止，停止操作不可用';
              const statusLabel = isStopping ? 'stopping...' : instance.status;
              const statusTone = isStopping ? 'warning' : launcherInstanceStatusTone(instance);
              return (
              <article key={instance.instance_id} className="rounded-lg border border-cyan-300/10 bg-slate-950/80 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate text-sm font-semibold text-slate-100">{instance.name}</h3>
                      <StatusBadge
                        color={statusTone}
                        variant="dot"
                        pulse={instance.status === 'running' || isStopping}
                        data-testid={`launcher-instance-status-${instance.instance_id}`}
                      >
                        {statusLabel}
                      </StatusBadge>
                    </div>
                    <p className="mt-1 truncate text-[11px] uppercase text-slate-500" title={instance.workspace}>
                      {instanceSubtitle(instance)}
                    </p>
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-md bg-white/[0.04] px-2 py-2">
                    <dt className="text-[10px] uppercase text-slate-500">backend</dt>
                    <dd className="mt-1 font-mono text-cyan-100">{instance.backend_port}</dd>
                  </div>
                  <div className="rounded-md bg-white/[0.04] px-2 py-2">
                    <dt className="text-[10px] uppercase text-slate-500">frontend</dt>
                    <dd className="mt-1 font-mono text-cyan-100">{instance.frontend_port}</dd>
                  </div>
                  <div className="col-span-2 rounded-md bg-white/[0.04] px-2 py-2">
                    <dt className="text-[10px] uppercase text-slate-500">workspace</dt>
                    <dd className="mt-1 truncate text-slate-300" title={instance.workspace}>{instance.workspace}</dd>
                  </div>
                </dl>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => openInstance(instance)} disabled={!isLauncherBackendOpenable(instance)}>
                    <ExternalLink className="h-3.5 w-3.5" />
                    {isLauncherBackendOpenable(instance) ? '打开' : '等待后端'}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void runAction(instance, 'restart')}
                    disabled={Boolean(actionId) || isCurrentControl}
                    title={isCurrentControl ? '当前控制后端不能自我重启' : undefined}
                    aria-label={`${restartActionLabel(instance)}实例 ${instance.instance_id}`}
                  >
                    {isRestarting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                    {isRestarting ? '正在重启...' : restartActionLabel(instance)}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void runAction(instance, 'stop')}
                    disabled={stopDisabled}
                    title={stopTitle}
                    aria-label={`停止实例 ${instance.instance_id}`}
                    data-testid={`launcher-instance-stop-${instance.instance_id}`}
                  >
                    {isStopping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
                    {isStopping ? '正在停止中...' : '停止'}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void runAction(instance, 'backend-logs')}>
                    <TerminalSquare className="h-3.5 w-3.5" />
                    后端日志
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setSelectedInstanceId(instance.instance_id)}>
                    <Info className="h-3.5 w-3.5" />
                    详情
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void runAction(instance, 'delete')}
                    disabled={Boolean(actionId) || isCurrentControl}
                    title={isCurrentControl ? '当前控制后端不能删除自身记录' : '删除实例记录'}
                    aria-label={`删除实例 ${instance.instance_id}`}
                  >
                    {isDeleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  </Button>
                </div>
              </article>
              );
            })}
            </div>
          </div>
        </section>
      </main>

      {logs ? (
        <aside className="fixed bottom-4 right-4 z-50 flex max-h-[50vh] w-[min(720px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-cyan-400/20 bg-slate-950 shadow-2xl">
          <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
            <div className="flex items-center gap-2 text-sm text-cyan-100">
              <Activity className="h-4 w-4" />
              {logs.instanceId} · {logs.stream}
            </div>
            <Button variant="ghost" size="sm" onClick={() => setLogs(null)}>关闭</Button>
          </div>
          <pre className="min-h-0 overflow-auto p-3 text-xs leading-relaxed text-slate-300">
            {logs.content || '暂无日志'}
          </pre>
        </aside>
      ) : null}

      {selectedInstance ? (
        <aside className="fixed right-4 top-20 z-40 flex max-h-[calc(100vh-6rem)] w-[min(560px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-cyan-400/20 bg-slate-950 shadow-2xl">
          <div className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="truncate text-sm font-semibold text-cyan-100">{selectedInstance.name}</h2>
                <StatusBadge color={launcherInstanceStatusTone(selectedInstance)} variant="dot">
                  {selectedInstance.status}
                </StatusBadge>
              </div>
              <p className="mt-1 truncate text-xs text-slate-500">
                {selectedInstance.instance_id} · {selectedInstance.kind}
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setSelectedInstanceId('')}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="min-h-0 space-y-4 overflow-auto p-4 text-xs">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
                <div className="text-[10px] uppercase text-slate-500">backend health</div>
                <div className="mt-1 text-sm font-semibold text-cyan-100">
                  {selectedInstance.backend_alive ? 'alive' : 'offline'}
                </div>
                <div className="mt-1 font-mono text-slate-500">{String(selectedInstance.metadata.backend_health || 'unknown')}</div>
              </div>
              <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
                <div className="text-[10px] uppercase text-slate-500">frontend health</div>
                <div className="mt-1 text-sm font-semibold text-cyan-100">
                  {selectedInstance.frontend_alive ? 'alive' : 'offline'}
                </div>
                <div className="mt-1 font-mono text-slate-500">{String(selectedInstance.metadata.frontend_health || 'unknown')}</div>
              </div>
            </div>

            <dl className="space-y-2">
              {[
                ['workspace', selectedInstance.workspace],
                ['runtime_root', selectedInstance.runtime_root],
                ['backend_url', selectedInstance.backend_url],
                ['frontend_url', selectedInstance.frontend_url || '(backend-only)'],
                ['open_url', buildInstanceWorkspaceUrl(selectedInstance)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-md border border-white/10 bg-slate-900/80 p-3">
                  <dt className="text-[10px] uppercase text-slate-500">{label}</dt>
                  <dd className="mt-1 break-all font-mono text-slate-200">{value}</dd>
                </div>
              ))}
            </dl>

            <div className="grid grid-cols-2 gap-2">
              <Button size="sm" onClick={() => openInstance(selectedInstance)}>
                <ExternalLink className="h-3.5 w-3.5" />
                打开实例
              </Button>
              <Button variant="outline" size="sm" onClick={() => void runAction(selectedInstance, 'frontend-logs')}>
                <TerminalSquare className="h-3.5 w-3.5" />
                前端日志
              </Button>
            </div>

            <section>
              <h3 className="text-[11px] font-semibold uppercase text-slate-500">bench metadata</h3>
              <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
                {formatJson(selectedInstance.bench)}
              </pre>
            </section>
            <section>
              <h3 className="text-[11px] font-semibold uppercase text-slate-500">instance metadata</h3>
              <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
                {formatJson(selectedInstance.metadata)}
              </pre>
            </section>
          </div>
        </aside>
      ) : null}
    </div>
  );
}
