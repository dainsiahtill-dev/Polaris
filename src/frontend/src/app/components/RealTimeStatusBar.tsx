import { Clock, Zap, PlayCircle, Square, Cpu, Database, Wifi } from 'lucide-react';
import { useState, useEffect } from 'react';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { AnimateCountUp } from '@/app/components/ui/animate-count-up';
import { AnimateBorder } from '@/app/components/ui/animate-border';
import { StatusBadge } from '@/app/components/ui/badge';

interface RealTimeStatusBarProps {
  pmRunning: boolean;
  directorRunning: boolean;
  pmStartedAt: number | null;
  directorStartedAt: number | null;
  pmIteration: number | null;
  llmStatus?: string;
  lancedbOk?: boolean;
}

function formatDuration(startedAt: number | null) {
  if (!startedAt) return '';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h${minutes % 60}m`;
}

export function RealTimeStatusBar({
  pmRunning,
  directorRunning,
  pmStartedAt,
  directorStartedAt,
  pmIteration,
  llmStatus,
  lancedbOk,
}: RealTimeStatusBarProps) {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const pmDuration = formatDuration(pmStartedAt);
  const directorDuration = formatDuration(directorStartedAt);

  return (
    <div className="h-11 bg-black/80 backdrop-blur-xl border-b border-accent/20 flex items-center px-5 relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-r from-accent/5 via-bg-tertiary to-accent/5" />

      {/* 左侧：系统状态 */}
      <div className="flex items-center gap-2 flex-1 relative z-10">

        {/* PM 状态 */}
        <AnimateBorder
          wrapperClassName="rounded-lg"
          glowColor="#c85040"
          glowSize={60}
          duration={4}
          rx="8"
          showOutline={false}
          outlineClassName="border-accent/20"
          className={pmRunning ? '' : 'opacity-0 pointer-events-none absolute'}
        >
          <div className="bg-bg-panel/80 backdrop-blur-md rounded-lg border border-accent/20 px-3 py-1.5 flex items-center gap-2">
            <div className="relative">
              {pmRunning ? (
                <>
                  <div className="absolute inset-0 bg-accent/30 blur-md rounded-full animate-pulse" />
                  <PlayCircle className="w-4 h-4 text-accent relative" />
                </>
              ) : (
                <Square className="w-4 h-4 text-text-dim" />
              )}
            </div>
            <div className="flex flex-col">
              <div className="text-[10px] font-semibold text-accent tracking-wide">{UI_TERMS.roles.pm}</div>
              <div className="text-[9px] text-text-muted font-mono">
                {pmRunning ? `${UI_TERMS.states.active} ${pmDuration}` : UI_TERMS.states.idle}
              </div>
            </div>
          </div>
        </AnimateBorder>

        {/* PM 静止状态（不运行时显示） */}
        {!pmRunning && (
          <div className="bg-bg-panel/80 backdrop-blur-md rounded-lg border border-border px-3 py-1.5 flex items-center gap-2">
            <Square className="w-4 h-4 text-text-dim" />
            <div className="flex flex-col">
              <div className="text-[10px] font-semibold text-text-dim tracking-wide">{UI_TERMS.roles.pm}</div>
              <div className="text-[9px] text-text-muted font-mono">{UI_TERMS.states.idle}</div>
            </div>
          </div>
        )}

        {/* Director 状态 */}
        <AnimateBorder
          wrapperClassName="rounded-lg"
          glowColor="#4a9e9e"
          glowSize={60}
          duration={5}
          rx="8"
          showOutline={false}
          className={directorRunning ? '' : 'opacity-0 pointer-events-none absolute'}
        >
          <div className="bg-bg-panel/80 backdrop-blur-md rounded-lg border border-status-info/20 px-3 py-1.5 flex items-center gap-2">
            <div className="relative">
              {directorRunning ? (
                <>
                  <div className="absolute inset-0 bg-status-info/30 blur-md rounded-full animate-pulse" />
                  <Cpu className="w-4 h-4 text-status-info relative" />
                </>
              ) : (
                <Square className="w-4 h-4 text-text-dim" />
              )}
            </div>
            <div className="flex flex-col">
              <div className="text-[10px] font-semibold text-status-info tracking-wide">{UI_TERMS.roles.director}</div>
              <div className="text-[9px] text-text-muted font-mono">
                {directorRunning ? `${UI_TERMS.states.active} ${directorDuration}` : UI_TERMS.states.idle}
              </div>
            </div>
          </div>
        </AnimateBorder>

        {/* Director 静止状态 */}
        {!directorRunning && (
          <div className="bg-bg-panel/80 backdrop-blur-md rounded-lg border border-border px-3 py-1.5 flex items-center gap-2">
            <Square className="w-4 h-4 text-text-dim" />
            <div className="flex flex-col">
              <div className="text-[10px] font-semibold text-text-dim tracking-wide">{UI_TERMS.roles.director}</div>
              <div className="text-[9px] text-text-muted font-mono">{UI_TERMS.states.idle}</div>
            </div>
          </div>
        )}

        {/* 轮次 — AnimateCountUp */}
        {pmIteration !== null && (
          <div className="bg-bg-panel/80 backdrop-blur-md rounded-lg border border-gold/30 px-3 py-1.5 flex items-center gap-2">
            <div className="relative">
              <div className="absolute inset-0 bg-gold/20 blur-md rounded-full" />
              <Zap className="w-4 h-4 text-gold relative" />
            </div>
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
          <div className="bg-bg-panel/80 backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5 border border-border">
            <Wifi className="w-3.5 h-3.5 text-accent shrink-0" />
            <div className="flex flex-col">
              <div className="text-[8px] text-text-muted font-mono tracking-wider">LLM</div>
              <StatusBadge
                color={llmStatus === 'ready' ? 'success' : llmStatus === 'blocked' ? 'error' : 'warning'}
                variant="dot"
                pulse={llmStatus === 'ready'}
                className="text-[9px] border-0 bg-transparent p-0"
              >
                {llmStatus === 'ready'
                  ? UI_TERMS.states.ready
                  : llmStatus === 'blocked'
                    ? UI_TERMS.states.blocked
                    : UI_TERMS.states.unknown}
              </StatusBadge>
            </div>
          </div>
        )}

        {/* 数据库状态 — StatusBadge */}
        {lancedbOk !== undefined && (
          <div className="bg-bg-panel/80 backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5 border border-border">
            <Database className="w-3.5 h-3.5 text-status-info shrink-0" />
            <div className="flex flex-col">
              <div className="text-[8px] text-text-muted font-mono tracking-wider">经籍库</div>
              <StatusBadge
                color={lancedbOk ? 'success' : 'error'}
                variant="dot"
                pulse={lancedbOk}
                className="text-[9px] border-0 bg-transparent p-0"
              >
                {lancedbOk ? UI_TERMS.states.ready : UI_TERMS.states.offline}
              </StatusBadge>
            </div>
          </div>
        )}

        {/* 时间 */}
        <div className="bg-bg-panel/80 backdrop-blur-sm rounded-lg px-2.5 py-1.5 flex items-center gap-1.5 border border-border">
          <Clock className="w-3.5 h-3.5 text-text-muted shrink-0" />
          <div className="flex flex-col">
            <div className="text-[8px] text-text-muted font-mono tracking-wider">漏刻时辰</div>
            <div className="text-[10px] font-mono text-text-main font-bold">
              {currentTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </div>
          </div>
        </div>
      </div>

      <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-border to-transparent" />
    </div>
  );
}
