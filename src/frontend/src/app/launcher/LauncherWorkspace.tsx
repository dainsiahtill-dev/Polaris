import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ExternalLink,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Server,
  Square,
  TerminalSquare,
  Trash2,
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

function statusTone(instance: PolarisInstance): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (instance.status === 'running' && instance.backend_alive) return 'success';
  if (instance.status === 'observed') return 'info';
  if (instance.backend_pid || instance.frontend_pid) return 'warning';
  return 'default';
}

function basename(path: string): string {
  const normalized = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return normalized.split('/').filter(Boolean).pop() || normalized || 'workspace';
}

function openInstance(instance: PolarisInstance): void {
  window.open(buildInstanceWorkspaceUrl(instance), '_blank', 'noopener,noreferrer');
}

const defaultForm: StartInstancePayload = {
  kind: 'project',
  workspace: '',
  name: '',
  backend_reload: true,
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
  const { subscribeChannels } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();
  const connection = useConnectionState();

  const runningCount = useMemo(
    () => instances.filter((item) => item.status === 'running' && item.backend_alive).length,
    [instances],
  );
  const benchCount = useMemo(
    () => instances.filter((item) => item.kind === 'bench_project').length,
    [instances],
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

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="border-b border-cyan-400/20 bg-slate-950/95 px-6 py-4">
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
          </div>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-4 p-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
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

        <section className="min-w-0 rounded-lg border border-white/10 bg-white/[0.035]">
          <div className="border-b border-white/10 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-100">实例</h2>
          </div>
          <div className="grid gap-3 p-4 md:grid-cols-2 2xl:grid-cols-3">
            {instances.length === 0 ? (
              <div className="col-span-full rounded-lg border border-dashed border-white/10 p-8 text-center text-sm text-slate-500">
                暂无实例
              </div>
            ) : instances.map((instance) => (
              <article key={instance.instance_id} className="rounded-lg border border-cyan-300/10 bg-slate-950/80 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate text-sm font-semibold text-slate-100">{instance.name}</h3>
                      <StatusBadge color={statusTone(instance)} variant="dot" pulse={instance.status === 'running'}>
                        {instance.status}
                      </StatusBadge>
                    </div>
                    <p className="mt-1 truncate text-[11px] uppercase text-slate-500" title={instance.workspace}>
                      {instance.instance_id} · {instance.kind}
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
                  <Button size="sm" onClick={() => openInstance(instance)}>
                    <ExternalLink className="h-3.5 w-3.5" />
                    打开
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void runAction(instance, 'restart')} disabled={Boolean(actionId)}>
                    <RotateCcw className="h-3.5 w-3.5" />
                    重启
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void runAction(instance, 'stop')} disabled={Boolean(actionId)}>
                    <Square className="h-3.5 w-3.5" />
                    停止
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void runAction(instance, 'backend-logs')}>
                    <TerminalSquare className="h-3.5 w-3.5" />
                    后端日志
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void runAction(instance, 'delete')} disabled={Boolean(actionId)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </article>
            ))}
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
    </div>
  );
}
