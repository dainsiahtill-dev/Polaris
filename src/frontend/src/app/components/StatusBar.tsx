import { Activity as ActivityIcon, CheckCircle, Clock, AlertTriangle, Zap, FileText } from 'lucide-react';

interface StatusBarProps {
  pmRunning: boolean;
  directorRunning: boolean;
  pmStartedAt: number | null;
  directorStartedAt: number | null;
  pmMode?: string | null;
  failures: number | null;
  iteration: number | null;
  pmBackend: string;
  directorModel: string;
  backendError?: string | null;
  onOpenLogs?: () => void;
  gitPresent?: boolean | null;
  pmError?: string | null;
  directorError?: string | null;
  ollamaError?: string | null;
  successes?: number | null;
  total?: number | null;
  rate?: number | null;
  onPingHealth?: () => void;
  healthStatus?: string | null;
  lancedbOk?: boolean | null;
  lancedbError?: string | null;
}

function formatDuration(startedAt: number | null) {
  if (!startedAt) return '-';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

export function StatusBar({
  pmRunning,
  directorRunning,
  pmStartedAt,
  directorStartedAt,
  pmMode,
  failures,
  iteration,
  pmBackend,
  directorModel,
  backendError,
  onOpenLogs,
  gitPresent,
  pmError,
  directorError,
  ollamaError,
  successes,
  total,
  rate,
  onPingHealth,
  healthStatus,
  lancedbOk,
  lancedbError,
}: StatusBarProps) {
  const pmDuration = pmRunning ? formatDuration(pmStartedAt) : '-';
  const directorDuration = directorRunning ? formatDuration(directorStartedAt) : '-';
  const pmModeLabel = pmRunning && pmMode ? ` (${pmMode})` : '';
  const backendState = backendError ? 'Error' : 'OK';
  const backendClass = backendError ? 'text-red-400' : 'text-green-400';
  const gitState = gitPresent === null ? '未知' : gitPresent ? 'OK' : '缺失';
  const gitClass =
    gitPresent === null ? 'text-gray-500' : gitPresent ? 'text-green-400' : 'text-yellow-400';
  const lancedbState =
    lancedbOk === null || lancedbOk === undefined ? '未知' : lancedbOk ? 'OK' : '缺失';
  const lancedbClass =
    lancedbOk === null || lancedbOk === undefined
      ? 'text-gray-500'
      : lancedbOk
        ? 'text-green-400'
        : 'text-red-400';
  const successLabel =
    typeof successes === 'number' && typeof total === 'number'
      ? `${successes}/${total}${typeof rate === 'number' ? ` (${Math.round(rate * 100)}%)` : ''}`
      : '—';

  return (
    <div className="fixed bottom-4 right-4 z-[60] flex items-center gap-3 px-3 py-1.5 rounded-full glass-bubble shadow-2xl border-white/10 animate-in fade-in slide-in-from-bottom-4 duration-500 hover:scale-105 transition-transform cursor-default">
      {/* 运行状态 */}
      <div className="flex items-center gap-2 pr-3 border-r border-white/10">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full shadow-[0_0_8px_currentColor] ${pmRunning ? 'bg-status-success text-status-success animate-pulse' : 'bg-text-dim text-text-dim'}`} />
          <span className="text-[10px] text-text-muted font-bold tracking-tight">PM</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full shadow-[0_0_8px_currentColor] ${directorRunning ? 'bg-status-secondary text-status-secondary animate-pulse' : 'bg-text-dim text-text-dim'}`} />
          <span className="text-[10px] text-text-muted font-bold tracking-tight">Chief Engineer</span>
        </div>
      </div>

      {/* 统计信息 */}
      <div className="flex items-center gap-3 pr-3 border-r border-white/10">
        <div className="flex items-center gap-1" title="QA Pass Rate">
          <CheckCircle className="size-3 text-status-success" />
          <span className="text-[10px] text-text-main font-bold">{successLabel}</span>
        </div>
        <div className="flex items-center gap-1" title="轮次">
          <Zap className="size-3 text-accent" />
          <span className="text-[10px] text-text-main">{iteration ?? '0'}</span>
        </div>
      </div>

      {/* 快捷操作 */}
      <div className="flex items-center gap-1">
        {onPingHealth && (
          <button onClick={onPingHealth} className="p-1 px-1.5 rounded-full hover:bg-white/10 text-text-dim hover:text-white transition-colors flex items-center gap-1">
            <ActivityIcon className="size-3" />
            <span className="text-[9px] uppercase font-bold">{healthStatus === 'ok' ? '在线' : healthStatus || 'Ping'}</span>
          </button>
        )}
        {onOpenLogs && (
          <button onClick={onOpenLogs} className="p-1 px-1.5 rounded-full hover:bg-white/10 text-text-dim hover:text-white transition-colors flex items-center gap-1">
            <FileText className="size-3" />
            <span className="text-[9px] uppercase font-bold">Logs</span>
          </button>
        )}
      </div>
    </div>
  );
}
