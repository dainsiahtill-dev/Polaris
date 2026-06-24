import { useCallback, useEffect, useMemo, useState } from 'react';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/app/components/ui/drawer';
import {
  AlertTriangle,
  CheckCircle,
  Database,
  Download,
  FileWarning,
  History,
  KeyRound,
  RefreshCw,
  Search,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { Badge } from '@/app/components/ui/badge';
import {
  getControlPlaneProjection,
  type ControlPlaneProjectProjection,
  type ControlPlaneProjection,
} from '@/services/controlPlane';

interface HistoryDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultLimit?: number;
  workspace?: string;
}

interface LedgerStatusView {
  label: string;
  detail: string;
  tone: 'ok' | 'hold' | 'fail';
}

function ledgerStatusView(projection: ControlPlaneProjection | null): LedgerStatusView {
  if (!projection) {
    return {
      label: '等待账本',
      detail: 'Control Plane Run Ledger projection 尚未加载',
      tone: 'hold',
    };
  }
  if (!projection.available) {
    return {
      label: '账本不可用',
      detail: projection.detail || 'Run Ledger projection endpoint unavailable',
      tone: 'fail',
    };
  }
  if (projection.projected <= 0) {
    return {
      label: '等待证据',
      detail: projection.detail || 'Run Ledger 尚无可投影项目',
      tone: 'hold',
    };
  }
  if (projection.ok) {
    return {
      label: '已验证',
      detail: `Run Ledger verified ${projection.projected}/${projection.total || projection.projected}`,
      tone: 'ok',
    };
  }
  if (projection.failed > 0) {
    return {
      label: '门禁失败',
      detail: projection.detail || `${projection.failed} 个账本投影失败`,
      tone: 'fail',
    };
  }
  if (projection.missing > 0) {
    return {
      label: '证据缺失',
      detail: projection.detail || `${projection.missing} 个项目缺少物理证据`,
      tone: 'hold',
    };
  }
  return {
    label: projection.status || '账本待定',
    detail: projection.detail || 'Run Ledger projection is not terminal',
    tone: 'hold',
  };
}

function toneClass(tone: LedgerStatusView['tone']): string {
  if (tone === 'ok') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  if (tone === 'fail') return 'border-red-500/30 bg-red-500/10 text-red-200';
  return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
}

function projectState(project: ControlPlaneProjectProjection): LedgerStatusView {
  if (project.ok) {
    return {
      label: 'PASS',
      detail: project.detail || 'physical evidence verified',
      tone: 'ok',
    };
  }
  if (project.failed_gate_count > 0 || !project.integrity_ok || !project.outcome_ok) {
    return {
      label: 'FAIL',
      detail: project.detail || 'gate or integrity evidence failed',
      tone: 'fail',
    };
  }
  if (project.missing.length > 0) {
    return {
      label: 'HOLD',
      detail: project.missing.join(', '),
      tone: 'hold',
    };
  }
  return {
    label: 'PENDING',
    detail: project.detail || 'waiting for ledger receipts',
    tone: 'hold',
  };
}

function statusIcon(view: LedgerStatusView) {
  if (view.tone === 'ok') return <CheckCircle className="h-4 w-4 text-emerald-300" />;
  if (view.tone === 'fail') return <XCircle className="h-4 w-4 text-red-300" />;
  return <AlertTriangle className="h-4 w-4 text-amber-300" />;
}

function statusBadge(view: LedgerStatusView) {
  return (
    <Badge variant="outline" className={`border ${toneClass(view.tone)}`}>
      {view.label}
    </Badge>
  );
}

function evidenceSummary(project: ControlPlaneProjectProjection): string {
  const modalities = project.evidence_modalities || {};
  const rows = Object.entries(modalities)
    .map(([name, summary]) => `${name}: ${summary.ok}/${summary.total}`)
    .filter(Boolean);
  if (rows.length > 0) return rows.join(' · ');
  if (project.evidence_policy) {
    const required = project.evidence_policy.required_modalities;
    return required.length > 0 ? `required: ${required.join(', ')}` : 'policy: optional evidence';
  }
  return 'evidence policy: not declared';
}

function projectSearchText(project: ControlPlaneProjectProjection): string {
  return [
    project.project_id,
    project.latest_token_id,
    project.detail,
    ...project.missing,
    evidenceSummary(project),
  ]
    .join(' ')
    .toLowerCase();
}

export function RunLedgerHistoryContent({ defaultLimit = 100, workspace }: Pick<HistoryDrawerProps, 'defaultLimit' | 'workspace'>) {
  const [projection, setProjection] = useState<ControlPlaneProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getControlPlaneProjection({ workspace, maxRuns: defaultLimit });
      if (!result.ok || !result.data) {
        setProjection(null);
        setError(result.error || 'Run Ledger projection unavailable');
        return;
      }
      setProjection(result.data);
    } catch (err) {
      setProjection(null);
      setError(err instanceof Error ? err.message : 'Run Ledger projection unavailable');
    } finally {
      setLoading(false);
    }
  }, [defaultLimit, workspace]);

  useEffect(() => {
    load();
  }, [load]);

  const projects = projection?.projects ?? [];
  const filteredProjects = useMemo(() => {
    if (!query.trim()) return projects;
    const q = query.toLowerCase();
    return projects.filter((project) => projectSearchText(project).includes(q));
  }, [projects, query]);

  const status = ledgerStatusView(projection);
  const exportHistory = () => {
    if (!projection) return;
    const payload = JSON.stringify(
      {
        source: 'control_plane_run_ledger_projection',
        workspace: workspace || '',
        projection,
        filtered_projects: filteredProjects,
      },
      null,
      2
    );
    const blob = new Blob([payload], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `run-ledger-history-${new Date().toISOString().split('T')[0]}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-col bg-[var(--ink-indigo)]">
      <div className="flex items-center justify-between border-b border-gray-800 p-4">
        <div className="flex min-w-0 items-center gap-2">
          <History className="h-5 w-5 shrink-0 text-cyan-300" />
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-gray-100">Run Ledger 案卷</h2>
            <div className="truncate text-xs text-gray-500">
              {workspace || 'current workspace'} · {projection?.audit_path || 'waiting for ledger'}
            </div>
          </div>
          <Badge variant="outline" className="border-cyan-500/30 bg-cyan-500/10 text-xs text-cyan-200">
            {filteredProjects.length} project
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} className="text-gray-400 hover:text-white">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={exportHistory}
            disabled={!projection}
            className="text-gray-400 hover:text-white"
          >
            <Download className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 border-b border-gray-800 p-4 text-xs md:grid-cols-4">
        <div className={`rounded border p-3 ${toneClass(status.tone)}`}>
          <div className="mb-1 flex items-center gap-2 font-semibold">
            {statusIcon(status)}
            {status.label}
          </div>
          <div className="text-[11px] opacity-80">{status.detail}</div>
        </div>
        <div className="rounded border border-cyan-500/20 bg-cyan-500/5 p-3 text-cyan-100">
          <div className="flex items-center gap-2 text-gray-400"><Database className="h-3.5 w-3.5" />Projection</div>
          <div className="mt-1 font-mono text-lg">{projection?.projected ?? 0}/{projection?.total ?? 0}</div>
        </div>
        <div className="rounded border border-red-500/20 bg-red-500/5 p-3 text-red-100">
          <div className="flex items-center gap-2 text-gray-400"><FileWarning className="h-3.5 w-3.5" />Failed Gates</div>
          <div className="mt-1 font-mono text-lg">{projection?.failed ?? 0}</div>
        </div>
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-3 text-amber-100">
          <div className="flex items-center gap-2 text-gray-400"><KeyRound className="h-3.5 w-3.5" />Missing Evidence</div>
          <div className="mt-1 font-mono text-lg">{projection?.missing ?? 0}</div>
        </div>
      </div>

      {projection?.compat_ledgers_included ? (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-200">
          compat ledger included · 内部测试账本只作为平台投影输入，不是正式 UI 的事实源。
        </div>
      ) : null}

      <div className="border-b border-gray-800 p-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            placeholder="搜索 project、job token、证据缺口..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="border-gray-700 bg-[#0b1020] pl-10 text-gray-200 placeholder-gray-500"
          />
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-3 p-4">
          {loading ? (
            <div className="py-8 text-center text-gray-500">加载 Run Ledger...</div>
          ) : error ? (
            <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-center text-red-200">
              账本读取失败: {error}
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="rounded border border-gray-700 bg-[#11172a] p-4 text-center text-gray-500">
              暂无 Run Ledger 投影记录
            </div>
          ) : (
            filteredProjects.map((project) => {
              const state = projectState(project);
              return (
                <article
                  key={`${project.project_id}-${project.latest_token_id}`}
                  className="rounded-lg border border-gray-700 bg-[#11172a] p-4 shadow-[0_0_32px_rgba(0,255,255,0.05)]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        {statusIcon(state)}
                        <span className="font-mono text-sm text-cyan-200">{project.project_id}</span>
                        {statusBadge(state)}
                      </div>
                      <div className="mt-2 break-words text-sm text-gray-300">{state.detail}</div>
                    </div>
                    <div className="shrink-0 rounded border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-right text-xs text-cyan-100">
                      <div className="text-gray-500">gates</div>
                      <div className="font-mono">{project.gate_count - project.failed_gate_count}/{project.gate_count}</div>
                    </div>
                  </div>

                  <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                    <div className="rounded border border-gray-700 bg-[#0b1020] p-2 text-gray-300">
                      <div className="text-gray-500">latest job token</div>
                      <div className="mt-1 break-all font-mono text-cyan-200">{project.latest_token_id || 'n/a'}</div>
                    </div>
                    <div className="rounded border border-gray-700 bg-[#0b1020] p-2 text-gray-300">
                      <div className="text-gray-500">evidence</div>
                      <div className="mt-1 break-words text-gray-200">{evidenceSummary(project)}</div>
                    </div>
                  </div>

                  {project.missing.length > 0 ? (
                    <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
                      <div className="font-semibold">missing evidence</div>
                      <div className="mt-1 break-words">{project.missing.join(', ')}</div>
                    </div>
                  ) : null}
                </article>
              );
            })
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

export function HistoryDrawer({ open, onOpenChange, defaultLimit, workspace }: HistoryDrawerProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} direction="right">
      <DrawerContent
        data-testid="history-drawer"
        className="left-auto right-0 top-0 bottom-0 h-dvh border-l border-gray-800 bg-[var(--ink-indigo)] data-[state=open]:!translate-x-0 data-[state=open]:!transform-none"
        style={{
          backgroundColor: 'rgb(18, 14, 42)',
          boxSizing: 'border-box',
          right: 0,
          width: 'min(42rem, calc(100vw - 2rem))',
          maxWidth: 'calc(100vw - 2rem)',
        }}
      >
        <DrawerTitle className="sr-only">Run Ledger 案卷</DrawerTitle>
        <DrawerDescription className="sr-only">
          查看 Control Plane Run Ledger 投影、物理证据、门禁结果和缺失项。
        </DrawerDescription>
        <div className="flex-1 overflow-hidden">
          <RunLedgerHistoryContent defaultLimit={defaultLimit} workspace={workspace} />
        </div>
      </DrawerContent>
    </Drawer>
  );
}
