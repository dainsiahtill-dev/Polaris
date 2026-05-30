import { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Database,
  Settings,
  FileText,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  BarChart3,
  Brain,
  ClipboardList,
  Coins,
  Trash2,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import {
  clearRoleKernelCache,
  getRoleKernelCacheStats,
  getRoleKernelLLMEvents,
  getRoleKernelTokenBudgetStats,
  getPmManagementHealth,
  getPmManagementStatus,
  getPmStartupDiagnostics,
  initializePmManagement,
  type PmManagementHealthResponse,
  type PmManagementInitResponse,
  type PmManagementStatusResponse,
  type RoleKernelCacheStats,
  type RoleKernelLLMEvent,
  type RoleKernelLLMEventsResponse,
  type RoleKernelTokenBudgetStats,
  type PmStartupDiagnosticsResponse,
} from '@/services/pmService';

interface DiagnosticsStatus {
  lancedb: PmStartupDiagnosticsResponse['lancedb'] | null;
  llm: PmStartupDiagnosticsResponse['llm'] | null;
  workspace: PmStartupDiagnosticsResponse['workspace'] | null;
  planningInput: PmStartupDiagnosticsResponse['planning_input'] | null;
}

interface KernelDiagnosticsStatus {
  cache: RoleKernelCacheStats | null;
  llmEvents: RoleKernelLLMEventsResponse | null;
  tokenBudget: RoleKernelTokenBudgetStats | null;
}

interface PmManagementDiagnosticsStatus {
  status: PmManagementStatusResponse | null;
  health: PmManagementHealthResponse | null;
  initResult: PmManagementInitResponse | null;
}

interface PMDiagnosticsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  workspace?: string;
}

interface LlmRoleEvidenceRow {
  role: string;
  ready: boolean;
  source: string;
  issue: string;
  testedModel: string;
  providerId: string;
}

function EndpointChip({
  endpoint,
  method,
  testId,
}: {
  endpoint: string;
  method?: string;
  testId?: string;
}) {
  return (
    <span
      className="shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500"
      title={endpoint}
      data-testid={testId}
      data-endpoint={endpoint}
    >
      {method ? `${method} API` : 'API'}
    </span>
  );
}

function evidenceEndpoint(endpoint: string, workspace = ''): string {
  const value = String(workspace || '').trim();
  if (!value) return endpoint;
  const separator = endpoint.includes('?') ? '&' : '?';
  return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readText(record: Record<string, unknown>, key: string): string {
  return String(record[key] || '').trim();
}

function llmRoleEvidenceRows(llm: PmStartupDiagnosticsResponse['llm'] | null): LlmRoleEvidenceRow[] {
  const details = readRecord(llm?.details);
  const roles = readRecord(details?.roles);
  if (!roles) return [];

  const roleOrder = [
    ...(llm?.required_ready_roles || []),
    ...(llm?.blocked_roles || []),
    ...Object.keys(roles),
  ];
  const seen = new Set<string>();
  return roleOrder
    .map((rawRole) => String(rawRole || '').trim().toLowerCase())
    .filter((role) => {
      if (!role || seen.has(role)) return false;
      seen.add(role);
      return true;
    })
    .map((role) => {
      const row = readRecord(roles[role]) || {};
      return {
        role,
        ready: Boolean(row.ready),
        source: readText(row, 'readiness_source') || 'unknown',
        issue: readText(row, 'readiness_issue') || 'ok',
        testedModel: readText(row, 'tested_model') || readText(row, 'model') || 'unknown',
        providerId: readText(row, 'provider_id') || readText(row, 'tested_provider_id') || 'unknown',
      };
    });
}

export function PMDiagnosticsPanel({ isOpen, onClose, workspace = '' }: PMDiagnosticsPanelProps) {
  const [status, setStatus] = useState<DiagnosticsStatus>({
    lancedb: null,
    llm: null,
    workspace: null,
    planningInput: null,
  });
  const [kernelStatus, setKernelStatus] = useState<KernelDiagnosticsStatus>({
    cache: null,
    llmEvents: null,
    tokenBudget: null,
  });
  const [managementStatus, setManagementStatus] = useState<PmManagementDiagnosticsStatus>({
    status: null,
    health: null,
    initResult: null,
  });
  const [loading, setLoading] = useState(false);
  const [kernelLoading, setKernelLoading] = useState(false);
  const [managementLoading, setManagementLoading] = useState(false);
  const [cacheClearing, setCacheClearing] = useState(false);
  const [managementInitializing, setManagementInitializing] = useState(false);
  const [error, setError] = useState('');
  const [kernelError, setKernelError] = useState('');
  const [managementError, setManagementError] = useState('');
  const [initProjectName, setInitProjectName] = useState('');
  const [initDescription, setInitDescription] = useState('');
  const [expanded, setExpanded] = useState<string[]>(['all']);

  const loadKernelDiagnostics = useCallback(async () => {
    setKernelLoading(true);
    setKernelError('');
    const errors: string[] = [];

    try {
      const [cacheResult, tokenResult, llmResult] = await Promise.all([
        getRoleKernelCacheStats('pm'),
        getRoleKernelTokenBudgetStats('pm'),
        getRoleKernelLLMEvents('pm', { limit: 5, workspace }),
      ]);

      setKernelStatus({
        cache: cacheResult.ok && cacheResult.data ? cacheResult.data : null,
        llmEvents: llmResult.ok && llmResult.data ? llmResult.data : null,
        tokenBudget: tokenResult.ok && tokenResult.data ? tokenResult.data : null,
      });

      if (!cacheResult.ok) {
        errors.push(cacheResult.error || 'PM LLM 缓存统计读取失败');
      }
      if (!tokenResult.ok) {
        errors.push(tokenResult.error || 'PM Token 预算统计读取失败');
      }
      if (!llmResult.ok) {
        errors.push(llmResult.error || 'PM LLM 事件读取失败');
      }
    } catch (err) {
      errors.push(err instanceof Error ? err.message : 'PM Kernel 诊断读取失败');
      setKernelStatus({ cache: null, llmEvents: null, tokenBudget: null });
    } finally {
      setKernelLoading(false);
    }

    setKernelError(errors.join('；'));
  }, [workspace]);

  const loadManagementDiagnostics = useCallback(async () => {
    setManagementLoading(true);
    setManagementError('');
    try {
      const statusResult = await getPmManagementStatus(workspace);
      if (!statusResult.ok || !statusResult.data) {
        setManagementStatus((current) => ({
          ...current,
          status: null,
          health: null,
        }));
        setManagementError(statusResult.error || 'PM 管理状态读取失败');
        return;
      }

      const nextStatus = statusResult.data;
      if (!nextStatus.initialized) {
        setManagementStatus((current) => ({
          ...current,
          status: nextStatus,
          health: null,
        }));
        return;
      }

      const healthResult = await getPmManagementHealth(workspace);
      setManagementStatus((current) => ({
        ...current,
        status: nextStatus,
        health: healthResult.ok && healthResult.data ? healthResult.data : null,
      }));
      if (!healthResult.ok) {
        setManagementError(healthResult.error || 'PM 项目健康读取失败');
      }
    } catch (err) {
      setManagementStatus((current) => ({
        ...current,
        status: null,
        health: null,
      }));
      setManagementError(err instanceof Error ? err.message : 'PM 管理诊断读取失败');
    } finally {
      setManagementLoading(false);
    }
  }, [workspace]);

  const runDiagnostics = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [result] = await Promise.all([
        getPmStartupDiagnostics(workspace),
        loadKernelDiagnostics(),
        loadManagementDiagnostics(),
      ]);
      if (result.ok && result.data) {
        setStatus({
          lancedb: result.data.lancedb,
          llm: result.data.llm,
          workspace: result.data.workspace,
          planningInput: result.data.planning_input || null,
        });
      } else {
        setError(result.error || 'PM 启动诊断读取失败');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'PM 启动诊断读取失败');
    } finally {
      setLoading(false);
    }
  }, [loadKernelDiagnostics, loadManagementDiagnostics, workspace]);

  const handleClearKernelCache = useCallback(async () => {
    setCacheClearing(true);
    setKernelError('');
    try {
      const result = await clearRoleKernelCache('pm');
      if (result.ok) {
        await loadKernelDiagnostics();
      } else {
        setKernelError(result.error || 'PM LLM 缓存清理失败');
      }
    } catch (err) {
      setKernelError(err instanceof Error ? err.message : 'PM LLM 缓存清理失败');
    } finally {
      setCacheClearing(false);
    }
  }, [loadKernelDiagnostics]);

  const handleInitializeManagement = useCallback(async () => {
    setManagementInitializing(true);
    setManagementError('');
    try {
      const result = await initializePmManagement(
        {
          projectName: initProjectName.trim(),
          description: initDescription.trim(),
        },
        workspace,
      );
      if (result.ok && result.data) {
        setManagementStatus((current) => ({
          ...current,
          initResult: result.data ?? null,
        }));
        await loadManagementDiagnostics();
      } else {
        setManagementError(result.error || 'PM 管理初始化失败');
      }
    } catch (err) {
      setManagementError(err instanceof Error ? err.message : 'PM 管理初始化失败');
    } finally {
      setManagementInitializing(false);
    }
  }, [initDescription, initProjectName, loadManagementDiagnostics, workspace]);

  useEffect(() => {
    if (isOpen) {
      void runDiagnostics();
    }
  }, [isOpen, runDiagnostics]);

  if (!isOpen) return null;

  const allReady =
    status.lancedb?.ok &&
    status.llm?.state === 'ready' &&
    status.workspace?.status === 'ok' &&
    status.workspace.docs_present &&
    status.planningInput?.ok;
  const roleEvidenceRows = llmRoleEvidenceRows(status.llm);
  const kernelDiagnosticStatus: 'success' | 'warning' | 'error' = kernelError
    ? 'error'
    : kernelStatus.cache || kernelStatus.tokenBudget
      ? 'success'
      : 'warning';
  const managementDiagnosticStatus: 'success' | 'warning' | 'error' = managementError
    ? 'error'
    : managementStatus.status?.initialized && managementStatus.health
      ? pmManagementHealthTone(managementStatus.health.overall)
      : 'warning';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-amber-500/20 bg-slate-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
              <Settings className="w-4 h-4 text-amber-400" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-100">PM 启动诊断</h2>
              <p className="text-xs text-slate-500">检查启动失败的常见原因</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} className="text-slate-400 hover:text-slate-200">
            关闭
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-6 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 text-amber-400 animate-spin mr-3" />
              <span className="text-slate-400">正在检查...</span>
            </div>
          ) : (
            <>
              {error && (
                <div
                  className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200"
                  data-testid="pm-diagnostics-error"
                >
                  {error}
                </div>
              )}

              {/* Overall Status */}
              <div
                className={cn(
                  'p-4 rounded-lg border',
                  allReady
                    ? 'bg-emerald-500/10 border-emerald-500/20'
                    : 'bg-red-500/10 border-red-500/20'
                )}
              >
                <div className="flex items-center gap-3">
                  {allReady ? (
                    <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  ) : (
                    <AlertCircle className="w-5 h-5 text-red-400" />
                  )}
                  <div>
                    <p className={cn('font-medium', allReady ? 'text-emerald-400' : 'text-red-400')}>
                      {allReady ? '所有检查通过' : '检测到问题'}
                    </p>
                    <p className="text-sm text-slate-400">
                      {allReady
                        ? 'PM 应该可以正常启动'
                        : '请解决以下问题后再尝试启动 PM'}
                    </p>
                  </div>
                </div>
              </div>

              {/* LanceDB Check */}
              <DiagnosticItem
                title="LanceDB 向量数据库"
                icon={<Database className="w-4 h-4" />}
                status={status.lancedb?.ok ? 'success' : 'error'}
                expanded={expanded.includes('lancedb')}
                onToggle={() => toggleExpanded('lancedb', expanded, setExpanded)}
              >
                {status.lancedb?.ok ? (
                  <p className="text-sm text-slate-300">LanceDB 正常运行</p>
                ) : (
                  <div className="space-y-2">
                    <p className="text-sm text-red-400">
                      错误: {status.lancedb?.error || 'LanceDB 未就绪'}
                    </p>
                    <div className="text-sm text-slate-400 space-y-1">
                      <p>解决方案:</p>
                      <ul className="list-disc list-inside ml-2 space-y-1">
                        <li>确保 LanceDB 已安装: pip install lancedb</li>
                        <li>检查 Python 环境是否正确</li>
                        <li>重启后端服务</li>
                      </ul>
                    </div>
                  </div>
                )}
              </DiagnosticItem>

              {/* LLM Config Check */}
              <DiagnosticItem
                title="LLM 配置"
                icon={<Settings className="w-4 h-4" />}
                status={
                  status.llm?.state === 'ready'
                    ? 'success'
                    : status.llm?.state === 'blocked'
                    ? 'error'
                    : 'warning'
                }
                expanded={expanded.includes('llm')}
                onToggle={() => toggleExpanded('llm', expanded, setExpanded)}
              >
                {status.llm?.state === 'ready' ? (
                  <div className="space-y-2">
                    <p className="text-sm text-slate-300">LLM 配置正常</p>
                    {roleEvidenceRows.length > 0 && (
                      <div className="space-y-1" data-testid="pm-llm-role-evidence">
                        {roleEvidenceRows.map((row) => (
                          <div key={row.role} className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-100">
                            {row.role}: ready · {row.source} · {row.providerId} · {row.testedModel}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="text-sm text-red-400">
                      状态: {status.llm?.state || '未知'}
                    </p>
                    {status.llm?.blocked_roles && status.llm.blocked_roles.length > 0 && (
                      <p className="text-sm text-slate-400">
                        阻塞的角色: {status.llm.blocked_roles.join(', ')}
                      </p>
                    )}
                    {roleEvidenceRows.length > 0 && (
                      <div className="space-y-1" data-testid="pm-llm-role-evidence">
                        {roleEvidenceRows.map((row) => (
                          <div key={row.role} className="rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1 text-xs text-red-100">
                            {row.role}: {row.issue} · {row.source} · {row.providerId} · {row.testedModel}
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="text-sm text-slate-400 space-y-1">
                      <p>解决方案:</p>
                      <ol className="list-decimal list-inside ml-2 space-y-1">
                        <li>打开设置 (Settings)</li>
                        <li>进入 LLM 设置标签</li>
                        <li>配置 PM 角色的 Provider 和 Model</li>
                        <li>运行 LLM 测试确保配置正确</li>
                      </ol>
                    </div>
                  </div>
                )}
              </DiagnosticItem>

              {/* Workspace Check */}
              <DiagnosticItem
                title="工作区"
                icon={<FileText className="w-4 h-4" />}
                status={status.workspace?.status === 'ok' && status.workspace.docs_present ? 'success' : 'error'}
                expanded={expanded.includes('workspace')}
                onToggle={() => toggleExpanded('workspace', expanded, setExpanded)}
              >
                {status.workspace?.status === 'ok' ? (
                  <div className="space-y-1">
                    <p className="text-sm text-slate-300">工作区已配置</p>
                    {!status.workspace.docs_present && (
                      <div className="space-y-2 text-sm">
                        <p className="text-red-300">docs/ 目录不存在，PM 启动已被阻断</p>
                        <div className="text-slate-400 space-y-1">
                          <p>解决方案:</p>
                          <ul className="list-disc list-inside ml-2 space-y-1">
                            <li>返回主界面完成 docs 初始化</li>
                            <li>确认工作区包含可审计的 docs/ 规划材料</li>
                          </ul>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="text-sm text-red-400">工作区未设置</p>
                    <div className="text-sm text-slate-400 space-y-1">
                      <p>解决方案:</p>
                      <ul className="list-disc list-inside ml-2 space-y-1">
                        <li>在主界面选择工作区目录</li>
                        <li>确保有写入权限</li>
                      </ul>
                    </div>
                  </div>
                )}
              </DiagnosticItem>

              <DiagnosticItem
                title="规划输入"
                icon={<ClipboardList className="w-4 h-4" />}
                status={status.planningInput?.ok ? 'success' : 'error'}
                expanded={expanded.includes('planning-input')}
                onToggle={() => toggleExpanded('planning-input', expanded, setExpanded)}
              >
                {status.planningInput?.ok ? (
                  <div className="space-y-2" data-testid="pm-planning-input-diagnostics">
                    <p className="text-sm text-slate-300">PM 已找到可规划输入</p>
                    <div className="grid gap-2 rounded-md border border-emerald-500/15 bg-emerald-500/10 p-3 text-xs text-emerald-50">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-emerald-200/80">来源</span>
                        <span className="font-mono">{formatPlanningInputSource(status.planningInput.source)}</span>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-emerald-200/80">字符/字节</span>
                        <span className="font-mono">
                          {formatNumber(status.planningInput.chars)} / {formatNumber(status.planningInput.bytes)}
                        </span>
                      </div>
                      <div className="min-w-0">
                        <div className="text-emerald-200/80">路径</div>
                        <div className="truncate font-mono text-[11px]" title={status.planningInput.path || ''}>
                          {status.planningInput.path || '-'}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3" data-testid="pm-planning-input-diagnostics">
                    <p className="text-sm text-red-300">
                      {status.planningInput?.status === 'empty'
                        ? '规划输入文件为空，PM 启动已被阻断'
                        : status.planningInput?.status === 'unreadable'
                          ? '规划输入无法读取，PM 启动已被阻断'
                          : '未找到需求或计划输入，PM 启动已被阻断'}
                    </p>
                    <div className="text-sm text-slate-400 space-y-1">
                      <p>解决方案:</p>
                      <ul className="list-disc list-inside ml-2 space-y-1">
                        <li>通过政事堂生成 docs/product/requirements.md</li>
                        <li>确认 runtime/contracts/requirements.md 或 plan.md 已同步</li>
                        <li>在 PM Workbench 中输入明确 directive 后再运行</li>
                      </ul>
                    </div>
                    {(status.planningInput?.checked_paths || []).length > 0 && (
                      <div className="space-y-1 rounded-md border border-white/10 bg-slate-950/50 p-2 text-[11px] text-slate-400">
                        {(status.planningInput?.checked_paths || []).slice(0, 5).map((path) => (
                          <div key={path} className="truncate font-mono" title={path}>{path}</div>
                        ))}
                      </div>
                    )}
                    {status.planningInput?.error ? (
                      <p className="text-xs text-red-200">错误: {status.planningInput.error}</p>
                    ) : null}
                  </div>
                )}
              </DiagnosticItem>

              <DiagnosticItem
                title="PM 管理状态"
                icon={<Settings className="w-4 h-4" />}
                status={managementDiagnosticStatus}
                expanded={expanded.includes('management')}
                onToggle={() => toggleExpanded('management', expanded, setExpanded)}
              >
                <div className="space-y-3" data-testid="pm-management-diagnostics">
                  {managementError ? (
                    <div
                      className="rounded-md border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200"
                      data-testid="pm-management-diagnostics-error"
                    >
                      {managementError}
                    </div>
                  ) : null}

                  {managementLoading ? (
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <Loader2 className="h-4 w-4 animate-spin text-amber-300" />
                      正在读取 PM 管理状态...
                    </div>
                  ) : (
                    <>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <ManagementMetricBlock
                          label="状态"
                          endpoint={evidenceEndpoint('/pm/v2/pm/status', workspace)}
                          endpointTestId="pm-management-status-endpoint"
                          rows={[
                            ['Initialized', String(managementStatus.status?.initialized ?? false)],
                            ['Workspace', managementStatus.status?.workspace || '-'],
                            ['Project', readManagementString(managementStatus.status, ['project', 'project_name']) || '-'],
                            ['Version', managementStatus.status?.version || '-'],
                          ]}
                        />
                        <ManagementMetricBlock
                          label="健康"
                          endpoint={evidenceEndpoint('/pm/v2/pm/health', workspace)}
                          endpointTestId="pm-management-health-endpoint"
                          rows={[
                            ['Overall', managementStatus.health?.overall || (managementStatus.status?.initialized ? 'unavailable' : 'not initialized')],
                            ['Components', String(Object.keys(managementStatus.health?.components || {}).length)],
                            ['Metrics', String(Object.keys(managementStatus.health?.metrics || {}).length)],
                            ['Advice', String(managementStatus.health?.recommendations?.length || 0)],
                          ]}
                        />
                      </div>

                      {managementStatus.health ? (
                        <div className="grid gap-2 rounded-md border border-white/10 bg-white/[0.035] p-3 text-xs text-slate-300">
                          <div className="flex flex-wrap gap-2">
                            {Object.entries(managementStatus.health.components).map(([name, value]) => (
                              <span key={name} className="rounded border border-white/10 bg-slate-950/55 px-2 py-1">
                                {name} · {value}
                              </span>
                            ))}
                          </div>
                          {managementStatus.health.recommendations.length > 0 ? (
                            <ul className="list-disc space-y-1 pl-4 text-[11px] text-slate-400">
                              {managementStatus.health.recommendations.slice(0, 4).map((recommendation) => (
                                <li key={recommendation}>{recommendation}</li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      ) : null}

                      {!managementStatus.status?.initialized ? (
                        <div
                          className="rounded-md border border-amber-500/20 bg-amber-500/10 p-3"
                          data-testid="pm-management-init-panel"
                        >
                          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-amber-100">
                            <span className="font-medium">PM 管理尚未初始化</span>
                            <EndpointChip
                              endpoint={evidenceEndpoint('/pm/v2/pm/init', workspace)}
                              method="POST"
                              testId="pm-management-init-endpoint"
                            />
                          </div>
                          <div className="grid gap-2 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto]">
                            <input
                              value={initProjectName}
                              onChange={(event) => setInitProjectName(event.target.value)}
                              placeholder="Project name"
                              data-testid="pm-management-init-project"
                              className="h-8 rounded-md border border-white/10 bg-slate-950/60 px-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none"
                            />
                            <input
                              value={initDescription}
                              onChange={(event) => setInitDescription(event.target.value)}
                              placeholder="Description"
                              data-testid="pm-management-init-description"
                              className="h-8 rounded-md border border-white/10 bg-slate-950/60 px-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none"
                            />
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => void handleInitializeManagement()}
                              disabled={managementInitializing}
                              data-testid="pm-management-init-submit"
                              className="border-amber-500/30 text-amber-100 hover:bg-amber-500/10"
                            >
                              {managementInitializing ? (
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
                              )}
                              初始化
                            </Button>
                          </div>
                        </div>
                      ) : null}

                      {managementStatus.initResult ? (
                        <div
                          className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100"
                          data-testid="pm-management-init-result"
                        >
                          initialized · {managementStatus.initResult.project_name || managementStatus.initResult.message || managementStatus.initResult.workspace}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              </DiagnosticItem>

              <DiagnosticItem
                title="LLM 缓存与预算"
                icon={<BarChart3 className="w-4 h-4" />}
                status={kernelDiagnosticStatus}
                expanded={expanded.includes('kernel')}
                onToggle={() => toggleExpanded('kernel', expanded, setExpanded)}
              >
                <div className="space-y-3" data-testid="pm-kernel-diagnostics">
                  {kernelError && (
                    <div
                      className="rounded-md border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200"
                      data-testid="pm-kernel-diagnostics-error"
                    >
                      {kernelError}
                    </div>
                  )}

                  {kernelLoading ? (
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <Loader2 className="h-4 w-4 animate-spin text-amber-300" />
                      正在读取 Kernel 统计...
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                      <KernelMetricBlock
                        icon={<Database className="h-3.5 w-3.5 text-cyan-300" />}
                        label="缓存"
                        endpoint="/v2/pm/cache-stats"
                        endpointTestId="pm-kernel-cache-endpoint"
                        rows={[
                          ['状态', kernelStatus.cache?.enabled === false ? '关闭' : '开启'],
                          ['命中率', formatPercent(kernelStatus.cache?.hit_rate)],
                          ['条目', `${formatNumber(kernelStatus.cache?.size)} / ${formatNumber(kernelStatus.cache?.max_size)}`],
                          ['命中/未命中', `${formatNumber(kernelStatus.cache?.hits)} / ${formatNumber(kernelStatus.cache?.misses)}`],
                        ]}
                      />
                      <KernelMetricBlock
                        icon={<Coins className="h-3.5 w-3.5 text-emerald-300" />}
                        label="Token 预算"
                        endpoint="/v2/pm/token-budget-stats"
                        endpointTestId="pm-kernel-token-budget-endpoint"
                        rows={[
                          ['总量', formatNumber(kernelStatus.tokenBudget?.total)],
                          ['对话可用', formatNumber(kernelStatus.tokenBudget?.available_conversation)],
                          ['系统/任务', `${formatNumber(kernelStatus.tokenBudget?.system_context)} / ${formatNumber(kernelStatus.tokenBudget?.task_context)}`],
                          ['安全边际', formatNumber(kernelStatus.tokenBudget?.safety_margin)],
                        ]}
                      />
                      <KernelMetricBlock
                        icon={<Brain className="h-3.5 w-3.5 text-indigo-300" />}
                        label="LLM 事件"
                        endpoint={evidenceEndpoint('/v2/pm/llm-events?limit=5', workspace)}
                        testId="pm-llm-events-diagnostics"
                        endpointTestId="pm-llm-events-endpoint"
                        rows={[
                          ['事件数', formatNumber(kernelStatus.llmEvents?.count ?? kernelStatus.llmEvents?.events?.length)],
                          ['最近类型', formatKernelEventType(kernelStatus.llmEvents?.events?.[0])],
                          ['最近模型', formatKernelEventModel(kernelStatus.llmEvents?.events?.[0])],
                          ['错误/重试', `${formatNumber(readStatNumber(kernelStatus.llmEvents?.stats, ['call_error', 'llm_error', 'errors']))} / ${formatNumber(readStatNumber(kernelStatus.llmEvents?.stats, ['call_retry', 'llm_retry', 'retries']))}`],
                        ]}
                      />
                    </div>
                  )}

                  <div className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-white/[0.035] px-3 py-2">
                    <div className="min-w-0 text-xs text-slate-400">
                      清理动作会调用后端缓存端点；不会修改工作区文件。
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void handleClearKernelCache()}
                      disabled={cacheClearing || kernelLoading}
                      data-testid="pm-kernel-cache-clear"
                      className="shrink-0 border-red-500/25 text-red-200 hover:bg-red-500/10"
                    >
                      {cacheClearing ? (
                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="mr-2 h-3.5 w-3.5" />
                      )}
                      清空缓存
                    </Button>
                  </div>
                </div>
              </DiagnosticItem>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-white/10">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void runDiagnostics()}
            disabled={loading}
            className="text-slate-400 hover:text-slate-200"
          >
            <RefreshCw className={cn('w-4 h-4 mr-2', loading && 'animate-spin')} />
            重新检查
          </Button>
          <Button variant="outline" size="sm" onClick={onClose} className="border-white/10 text-slate-300 hover:bg-white/5">
            知道了
          </Button>
        </div>
      </div>
    </div>
  );
}

function formatNumber(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '-';
}

function formatPercent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '-';
}

function formatPlanningInputSource(source: string | null | undefined): string {
  const labels: Record<string, string> = {
    runtime_requirements: 'runtime requirements',
    workspace_requirements: 'workspace requirements',
    legacy_requirements: 'legacy requirements',
    runtime_plan: 'runtime plan',
    workspace_plan: 'workspace plan',
  };
  const token = String(source || '').trim();
  return labels[token] || token || '-';
}

function readManagementString(record: Record<string, unknown> | null | undefined, keys: string[]): string {
  if (!record) {
    return '';
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
  }
  return '';
}

function pmManagementHealthTone(overall: string): 'success' | 'warning' | 'error' {
  const token = overall.trim().toLowerCase();
  if (['healthy', 'ok', 'ready', 'pass', 'passed'].includes(token)) {
    return 'success';
  }
  if (['failed', 'error', 'unhealthy', 'blocked'].includes(token)) {
    return 'error';
  }
  return 'warning';
}

function readEventText(event: RoleKernelLLMEvent | null | undefined, keys: string[]): string {
  if (!event) {
    return '';
  }
  for (const key of keys) {
    const value = event[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
  }
  return '';
}

function readStatNumber(stats: Record<string, unknown> | null | undefined, keys: string[]): number | null {
  if (!stats) {
    return null;
  }
  for (const key of keys) {
    const value = stats[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
      return Number(value);
    }
  }
  return null;
}

function formatKernelEventType(event: RoleKernelLLMEvent | null | undefined): string {
  const eventType = readEventText(event, ['event_type', 'type', 'name']);
  return eventType ? eventType.replace(/_/g, ' ') : '-';
}

function formatKernelEventModel(event: RoleKernelLLMEvent | null | undefined): string {
  return readEventText(event, ['model', 'provider', 'provider_type']) || '-';
}

function KernelMetricBlock({
  icon,
  label,
  endpoint,
  rows,
  testId,
  endpointTestId,
}: {
  icon: React.ReactNode;
  label: string;
  endpoint: string;
  rows: Array<[string, string]>;
  testId?: string;
  endpointTestId?: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/10 bg-white/[0.035] p-3" data-testid={testId} data-endpoint={endpoint}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-200">
          {icon}
          <span className="truncate">{label}</span>
        </div>
        <EndpointChip endpoint={endpoint} testId={endpointTestId} />
      </div>
      <div className="space-y-1">
        {rows.map(([name, value]) => (
          <div key={name} className="flex items-center justify-between gap-2 text-[11px]">
            <span className="text-slate-500">{name}</span>
            <span className="min-w-0 truncate font-mono text-slate-300" title={value}>
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ManagementMetricBlock({
  label,
  endpoint,
  rows,
  endpointTestId,
}: {
  label: string;
  endpoint: string;
  rows: Array<[string, string]>;
  endpointTestId?: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/10 bg-white/[0.035] p-3" data-endpoint={endpoint}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">{label}</span>
        <EndpointChip endpoint={endpoint} testId={endpointTestId} />
      </div>
      <div className="space-y-1">
        {rows.map(([name, value]) => (
          <div key={name} className="flex items-center justify-between gap-2 text-[11px]">
            <span className="text-slate-500">{name}</span>
            <span className="min-w-0 truncate font-mono text-slate-300" title={value}>
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Helper Components
interface DiagnosticItemProps {
  title: string;
  icon: React.ReactNode;
  status: 'success' | 'warning' | 'error';
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function DiagnosticItem({ title, icon, status, expanded, onToggle, children }: DiagnosticItemProps) {
  const statusColors = {
    success: 'border-emerald-500/20 bg-emerald-500/5',
    warning: 'border-amber-500/20 bg-amber-500/5',
    error: 'border-red-500/20 bg-red-500/5',
  };

  const statusIcons = {
    success: <CheckCircle2 className="w-4 h-4 text-emerald-400" />,
    warning: <AlertCircle className="w-4 h-4 text-amber-400" />,
    error: <AlertCircle className="w-4 h-4 text-red-400" />,
  };

  return (
    <div className={cn('rounded-lg border', statusColors[status])}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 text-left"
      >
        <div className="flex items-center gap-3">
          <div className="text-slate-400">{icon}</div>
          <span className="font-medium text-slate-200">{title}</span>
        </div>
        <div className="flex items-center gap-2">
          {statusIcons[status]}
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-slate-500" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-500" />
          )}
        </div>
      </button>
      {expanded && <div className="px-4 pb-4 border-t border-white/5 pt-3">{children}</div>}
    </div>
  );
}

function toggleExpanded(
  key: string,
  expanded: string[],
  setExpanded: (value: string[]) => void
) {
  if (expanded.includes(key)) {
    setExpanded(expanded.filter((k) => k !== key));
  } else {
    setExpanded([...expanded, key]);
  }
}
