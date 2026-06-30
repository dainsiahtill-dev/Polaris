import { Clock, Zap, PlayCircle, Square, Cpu, Database, Wifi, FileCode } from 'lucide-react';
import { useState, useEffect } from 'react';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { AnimateCountUp } from '@/app/components/ui/animate-count-up';
import { StatusBadge } from '@/app/components/ui/badge';
import type { FileEditEvent } from '@/app/hooks/useRuntimeStore';
import { normalizeStartedAtSeconds } from '@/app/utils/runtimeDisplay';

interface RealTimeStatusBarProps {
  pmRunning: boolean;
  directorRunning: boolean;
  pmStartedAt: number | null;
  directorStartedAt: number | null;
  pmIteration: number | null;
  llmStatus?: string;
  lancedbOk?: boolean;
  fileEditEvents?: FileEditEvent[];
}

function formatDuration(startedAt: number | null) {
  const normalizedStartedAt = normalizeStartedAtSeconds(startedAt);
  if (!normalizedStartedAt) return '';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - normalizedStartedAt));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h${minutes % 60}m`;
}

function activeLabel(duration: string) {
  return [UI_TERMS.states.active, duration].filter(Boolean).join(' ');
}

function displayRuntimeStatus(status: string) {
  if (status === 'ready') return '就绪';
  if (status === 'blocked') return '阻塞';
  return '未判';
}

export function RealTimeStatusBar({
  pmRunning,
  directorRunning,
  pmStartedAt,
  directorStartedAt,
  pmIteration,
  llmStatus,
  lancedbOk,
  fileEditEvents = [],
}: RealTimeStatusBarProps) {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    // UI-only clock tick for elapsed-time display; no network request is made.
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const pmDuration = formatDuration(pmStartedAt);
  const directorDuration = formatDuration(directorStartedAt);
  const latestFileEdit = fileEditEvents
    .filter((event) => Boolean(event.filePath))
    .slice()
    .sort((a, b) => Date.parse(String(b.timestamp || '')) - Date.parse(String(a.timestamp || '')))[0] || null;

  return (
    <div className="soft-panel-subtle h-11 backdrop-blur-xl border-b border-white/10 flex items-center px-5 relative overflow-hidden">

      {/* 左侧：系统状态 */}
      <div className="flex items-center gap-2 flex-1 relative z-10">

        {/* PM 状态 */}
        <div className="soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2">
          {pmRunning ? (
            <PlayCircle className="w-4 h-4 text-accent" />
          ) : (
            <Square className="w-4 h-4 text-text-dim" />
          )}
          <div className="flex flex-col">
            <div className="text-[10px] font-semibold text-accent tracking-wide">{UI_TERMS.roles.pm}</div>
            <div className="text-[9px] text-text-muted font-mono">
              {pmRunning ? activeLabel(pmDuration) : UI_TERMS.states.idle}
            </div>
          </div>
        </div>

        {/* Director 状态 */}
        <div className="soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2">
          {directorRunning ? (
            <Cpu className="w-4 h-4 text-status-info" />
          ) : (
            <Square className="w-4 h-4 text-text-dim" />
          )}
          <div className="flex flex-col">
            <div className="text-[10px] font-semibold text-status-info tracking-wide">{UI_TERMS.roles.director}</div>
            <div className="text-[9px] text-text-muted font-mono">
              {directorRunning ? activeLabel(directorDuration) : UI_TERMS.states.idle}
            </div>
          </div>
        </div>

        {/* 轮次 */}
        {pmIteration !== null && (
          <div className="soft-chip backdrop-blur-md rounded-lg px-3 py-1.5 flex items-center gap-2">
            <Zap className="w-4 h-4 text-gold" />
            <div className="flex flex-col">
              <div className="text-[10px] font-bold text-gold tracking-wider">轮次</div>
              <AnimateCountUp
                to={pmIteration}
                prefix="#"
                padStart={3}
                duration={0.8}
                className="text-[9px] text-gold font-mono font-bold"
              />
            </div>
          </div>
        )}
      </div>

      {/* 右侧：系统监控 */}
      <div className="flex items-center gap-2 flex-1 justify-end relative z-10">

        {/* LLM 状态 — StatusBadge */}
        {llmStatus && (
          <div className="soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5">
            <Wifi className="w-3.5 h-3.5 text-accent shrink-0" />
            <div className="flex flex-col">
              <div className="text-[8px] text-text-muted font-mono tracking-wider">LLM</div>
              <StatusBadge
                color={llmStatus === 'ready' ? 'success' : llmStatus === 'blocked' ? 'error' : 'warning'}
                variant="dot"
                pulse={llmStatus === 'ready'}
                className="text-[9px] border-0 bg-transparent p-0"
              >
                {displayRuntimeStatus(llmStatus)}
              </StatusBadge>
            </div>
          </div>
        )}

        {/* 数据库状态 — StatusBadge */}
        {lancedbOk !== undefined && (
          <div className="soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5 text-status-info shrink-0" />
            <div className="flex flex-col">
              <div className="text-[8px] text-text-muted font-mono tracking-wider">经籍库</div>
              <StatusBadge
                color={lancedbOk ? 'success' : 'error'}
                variant="dot"
                pulse={lancedbOk}
                className="text-[9px] border-0 bg-transparent p-0"
              >
                {lancedbOk ? '就绪' : '离线'}
              </StatusBadge>
            </div>
          </div>
        )}

        {latestFileEdit && (
          <div
            className="soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5 max-w-[240px]"
            data-testid="runtime-file-edit-status"
            title={latestFileEdit.filePath}
          >
            <FileCode className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
            <div className="flex min-w-0 flex-col">
              <div className="text-[8px] text-text-muted font-mono tracking-wider">文件变更</div>
              <div className="truncate text-[10px] font-mono text-text-main">
                {latestFileEdit.operation} {latestFileEdit.filePath}
              </div>
            </div>
          </div>
        )}

        {/* 时间 */}
        <div className="soft-chip backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5 text-text-muted shrink-0" />
          <div className="flex flex-col">
            <div className="text-[8px] text-text-muted font-mono tracking-wider">漏刻时辰</div>
            <div className="text-[10px] font-mono text-text-main font-bold">
              {currentTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
