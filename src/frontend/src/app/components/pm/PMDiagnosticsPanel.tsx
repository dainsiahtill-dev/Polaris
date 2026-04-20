import { useState, useEffect } from 'react';
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
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

interface DiagnosticsStatus {
  lancedb: {
    ok: boolean;
    error?: string;
  } | null;
  llm: {
    state: string;
    blocked_roles?: string[];
    required_ready_roles?: string[];
  } | null;
  workspace: {
    status: string;
    docs_present?: boolean;
    error?: string;
  } | null;
}

interface PMDiagnosticsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function PMDiagnosticsPanel({ isOpen, onClose }: PMDiagnosticsPanelProps) {
  const [status, setStatus] = useState<DiagnosticsStatus>({
    lancedb: null,
    llm: null,
    workspace: null,
  });
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string[]>(['all']);

  const runDiagnostics = async () => {
    setLoading(true);
    try {
      // Check LanceDB
      const lancedbRes = await apiFetch('/lancedb/status');
      const lancedb = lancedbRes.ok ? await lancedbRes.json() : { ok: false, error: 'Failed to check' };

      // Check LLM status
      const llmRes = await apiFetch('/llm/status');
      const llm = llmRes.ok ? await llmRes.json() : { state: 'unknown' };

      // Check workspace status
      const settingsRes = await apiFetch('/settings');
      const settings = settingsRes.ok ? await settingsRes.json() : {};
      const workspace = {
        status: settings.workspace ? 'ok' : 'missing',
        docs_present: settings.docs_present,
      };

      setStatus({ lancedb, llm, workspace });
    } catch (err) {
      devLogger.error('Diagnostics failed:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      runDiagnostics();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const allReady =
    status.lancedb?.ok &&
    status.llm?.state === 'ready' &&
    status.workspace?.status === 'ok';

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
                  <p className="text-sm text-slate-300">LLM 配置正常</p>
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
                status={status.workspace?.status === 'ok' ? 'success' : 'error'}
                expanded={expanded.includes('workspace')}
                onToggle={() => toggleExpanded('workspace', expanded, setExpanded)}
              >
                {status.workspace?.status === 'ok' ? (
                  <div className="space-y-1">
                    <p className="text-sm text-slate-300">工作区已配置</p>
                    {!status.workspace.docs_present && (
                      <p className="text-sm text-amber-400">
                        警告: docs/ 目录不存在，但这不是启动失败的直接原因
                      </p>
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
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-white/10">
          <Button
            variant="ghost"
            size="sm"
            onClick={runDiagnostics}
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
