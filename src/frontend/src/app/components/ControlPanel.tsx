import { Anchor, Play, Square, Settings, FolderOpen, RefreshCw, Zap, Loader2, FastForward, FileText, Brain, Activity, TerminalSquare, Crown, Hammer, MoreHorizontal, Bot, ClipboardList, Gauge } from 'lucide-react';
import { WindowControls } from './WindowControls';
import { UsageHUD, type UsageStats } from './UsageHUD';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { MiniStatusBadge } from '@/app/components/ai-dialogue/ManusStyleStatusIndicator';
import { cleanRuntimeDisplayText } from '@/app/utils/runtimeDisplay';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

interface ControlPanelProps {
  workspace: string;
  pmRunning: boolean;
  directorRunning: boolean;
  pmToggleDisabled?: boolean;
  directorToggleDisabled?: boolean;
  directorBlockedReason?: string;
  runOnceDisabled?: boolean;
  agentsNeeded?: boolean;
  agentsDraftReady?: boolean;
  agentsDraftFailed?: boolean;
  onOpenAgentsReview?: () => void;
  onGenerateAgentsDraft?: () => void;
  onOpenSettings: () => void;
  onPickWorkspace?: () => void;
  onTogglePm: () => void;
  onRunPmOnce?: () => void;
  onResumePm?: () => void;
  onToggleDirector: () => void;
  onStopOllama?: () => void;
  onRefresh: () => void;
  onOpenBrain?: () => void;
  onEnterPMWorkspace?: () => void;
  onEnterChiefEngineerWorkspace?: () => void;
  onEnterDirectorWorkspace?: () => void;
  onEnterFactoryMode?: () => void;
  onEnterAGIWorkspace?: () => void;
  onEnterRuntimeDiagnostics?: () => void;
  workspaceError?: string | null;
  isStartingPM?: boolean;
  isStoppingPM?: boolean;
  isStartingDirector?: boolean;
  isStoppingDirector?: boolean;
  isStoppingOllama?: boolean;
  healthStatus?: string | null;
  onPingHealth?: () => void;
  onOpenLogs?: () => void;
  isArtifactsOpen: boolean;
  onToggleArtifacts: () => void;
  usageStats?: UsageStats | null;
  ioFsyncMode?: string;
  memoryRefsMode?: string;
  onToggleTerminal?: () => void;
  isTerminalOpen?: boolean;
  // 新增：即时反馈相关
  currentPhase?: string;
  currentTask?: string;
  isExecutingTool?: boolean;
  currentToolName?: string;
}

export function ControlPanel({
  workspace,
  pmRunning,
  directorRunning,
  pmToggleDisabled,
  directorToggleDisabled,
  directorBlockedReason,
  runOnceDisabled,
  agentsNeeded,
  agentsDraftReady,
  agentsDraftFailed,
  onOpenAgentsReview,
  onGenerateAgentsDraft,
  onOpenSettings,
  onPickWorkspace,
  onTogglePm,
  onRunPmOnce,
  onResumePm,
  onToggleDirector,
  onStopOllama,
  onRefresh,
  onOpenBrain,
  onEnterPMWorkspace,
  onEnterChiefEngineerWorkspace,
  onEnterDirectorWorkspace,
  onEnterFactoryMode,
  onEnterAGIWorkspace,
  onEnterRuntimeDiagnostics,
  workspaceError,
  isStartingPM,
  isStoppingPM,
  isStartingDirector,
  isStoppingDirector,
  isStoppingOllama,
  healthStatus,
  onPingHealth,
  onOpenLogs,
  isArtifactsOpen,
  onToggleArtifacts,
  usageStats,
  ioFsyncMode,
  memoryRefsMode,
  onToggleTerminal,
  isTerminalOpen,
  currentPhase,
  currentTask,
  isExecutingTool,
  currentToolName,
}: ControlPanelProps) {
  const pmDisabled = !!pmToggleDisabled;
  const directorDisabled = !!directorToggleDisabled;
  const runOnceBlocked = !!runOnceDisabled;
  const showAgents = !!agentsNeeded;
  const agentsReady = !!agentsDraftReady || !!agentsDraftFailed;
  const normalizedIoMode = ioFsyncMode === 'relaxed' ? 'RELAXED' : ioFsyncMode ? 'STRICT' : '';
  const normalizedMemMode =
    memoryRefsMode === 'off' ? 'OFF' : memoryRefsMode === 'soft' ? 'SOFT' : memoryRefsMode ? 'STRICT' : '';
  const ioTone =
    ioFsyncMode === 'relaxed'
      ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
      : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  const memTone =
    memoryRefsMode === 'off'
      ? 'border-red-500/30 bg-red-500/10 text-red-200'
      : memoryRefsMode === 'soft'
        ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
        : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  const displayCurrentTask = cleanRuntimeDisplayText(currentTask);
  const displayCurrentToolName = cleanRuntimeDisplayText(currentToolName);

  // 计算当前状态指示
  const getStatusIndicator = () => {
    if (pmRunning || directorRunning) {
      if (currentPhase) {
        return { phase: currentPhase, task: currentTask, tool: currentToolName };
      }
      return { phase: pmRunning ? 'pm_running' : 'director_running', task: undefined, tool: undefined };
    }
    return { phase: 'idle', task: undefined, tool: undefined };
  };

  const statusInfo = getStatusIndicator();

  return (
    <header className="panel-header z-50 relative">
      {/* Logo 和标题 */}
      <div className="flex items-center gap-4">
        <WindowControls />
        <div className="w-px h-4 bg-white/10" />
        <div className="flex items-center gap-2 group">
          <div className="relative">
            <button
              onClick={onToggleArtifacts}
              className={`p-2 rounded-lg transition-colors ${isArtifactsOpen
                  ? 'text-accent bg-accent/10 hover:bg-accent/20'
                  : 'text-text-muted hover:text-text-main hover:bg-white/5'
                }`}
              title={isArtifactsOpen ? '收起监控面板' : '展开监控面板'}
            >
              <Activity className="size-5" />
            </button>
          </div>

          <div className="relative group/ws">

            <Anchor className="size-6 text-accent relative z-10" />
          </div>
          <div>
            <h1 className="font-heading font-bold text-xl text-text-main tracking-tight">Polaris</h1>
            <p className="text-[10px] text-text-dim font-mono tracking-wider uppercase">贞观法度 · 枢密中台</p>
          </div>
        </div>
      </div>

      {/* Workspace */}
      <div className="flex-1 max-w-lg mx-8 relative group">
        <div
          className={`no-drag flex items-center gap-2 bg-bg-panel/50 backdrop-blur-sm rounded-lg px-3 py-1.5 border transition-all duration-300 ${workspaceError ? 'border-status-error/60 shadow-[0_0_10px_rgba(239,68,68,0.2)]' : 'border-white/10 group-hover:border-accent/30 group-hover:shadow-glow'
            }`}
          title={workspaceError || undefined}
        >
          {onPickWorkspace ? (
            <button
              type="button"
              onClick={onPickWorkspace}
              className="text-text-muted hover:text-accent transition-colors"
              aria-label={`浏览并选定${UI_TERMS.nouns.workspace}`}
              title={`浏览并选定${UI_TERMS.nouns.workspace}`}
            >
              <FolderOpen className="size-4" />
            </button>
          ) : (
            <FolderOpen className="size-4 text-text-dim" />
          )}
          <input
            type="text"
            value={workspace}
            readOnly
            className="flex-1 bg-transparent text-sm text-text-main outline-none font-sans placeholder:text-text-dim/50 cursor-default"
            placeholder={`请点击左侧按钮选定${UI_TERMS.nouns.workspace}（Workspace）...`}
            aria-invalid={workspaceError ? true : undefined}
            aria-describedby={workspaceError ? 'workspace-error' : undefined}
          />
        </div>
        {workspaceError ? (
          <div
            id="workspace-error"
            className="absolute left-0 right-0 top-full mt-1 text-xs text-status-error bg-bg-panel border border-status-error/30 rounded px-2 py-1 shadow-lg z-50 backdrop-blur-md"
          >
            {workspaceError}
          </div>
        ) : null}
      </div>

      {/* 控制按钮 */}
      <div className="flex items-center gap-3">
        {/* PM 控制 */}
        <div className="no-drag flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg border border-white/5 backdrop-blur-sm">
          <span className="text-[10px] uppercase font-bold text-text-dim tracking-wider px-1">{UI_TERMS.roles.pm}</span>
          <button
            onClick={onTogglePm}
            data-testid="control-panel-pm-toggle"
            disabled={pmDisabled || isStartingPM || isStoppingPM}
            className={`p-1.5 rounded-md transition-all duration-300 relative ${pmRunning
              ? 'bg-gradient-primary text-white shadow-glow'
              : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'
              } ${pmDisabled || isStartingPM || isStoppingPM ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`}
            title={pmRunning ? UI_TERMS.actions.stopLoop : UI_TERMS.actions.startLoop}
          >
            {isStartingPM || isStoppingPM ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : pmRunning ? (
              <Square className="size-3.5 fill-current" />
            ) : (
              <Play className="size-3.5 fill-current" />
            )}
          </button>
          {onRunPmOnce ? (
            <button
              onClick={onRunPmOnce}
              data-testid="control-panel-pm-run-once"
              disabled={runOnceBlocked || isStartingPM}
              className={`p-1.5 rounded-md transition-colors text-text-muted hover:text-accent hover:bg-accent-dim relative ${runOnceBlocked || isStartingPM ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''
                }`}
              title={UI_TERMS.actions.runOnce}
            >
              {isStartingPM ? <Loader2 className="size-3.5 animate-spin" /> : <Zap className="size-3.5" />}
            </button>
          ) : null}
          {onResumePm && !pmRunning ? (
            <button
              onClick={onResumePm}
              disabled={pmDisabled || isStartingPM || isStoppingPM}
              className={`p-1.5 rounded-md transition-colors text-text-muted hover:text-status-warning hover:bg-status-warning/10 relative ${pmDisabled || isStartingPM || isStoppingPM ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''
                }`}
              title={UI_TERMS.actions.resumeLast}
            >
              <FastForward className="size-3.5" />
            </button>
          ) : null}
        </div>

        {/* Director 控制 */}
        <div className="no-drag flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg border border-white/5 backdrop-blur-sm">
          <span className="text-[10px] uppercase font-bold text-text-dim tracking-wider px-1">{UI_TERMS.roles.director}</span>
          {directorBlockedReason ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-status-error/20 text-status-error border border-status-error/20">
              {directorBlockedReason}
            </span>
          ) : null}
          <button
            onClick={onToggleDirector}
            data-testid="control-panel-director-toggle"
            disabled={directorDisabled || isStartingDirector || isStoppingDirector}
            className={`p-1.5 rounded-md transition-all duration-300 relative ${directorRunning
              ? 'bg-gradient-to-r from-accent-secondary to-blue-600 text-white shadow-[0_0_15px_rgba(6,182,212,0.4)]'
              : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'
              } ${directorDisabled || isStartingDirector || isStoppingDirector ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`}
            title={directorBlockedReason || undefined}
          >
            {isStartingDirector || isStoppingDirector ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : directorRunning ? (
              <Square className="size-3.5 fill-current" />
            ) : (
              <Play className="size-3.5 fill-current" />
            )}
          </button>
        </div>

        {/* Factory 模式按钮 */}
        {onEnterFactoryMode && (
          <button
            onClick={onEnterFactoryMode}
            className="no-drag p-1.5 rounded-md transition-all bg-gradient-to-r from-emerald-600 to-teal-600 text-white hover:shadow-[0_0_15px_rgba(16,185,129,0.4)]"
            title="Factory 模式 - 无人值守开发工厂"
          >
            <Hammer className="size-3.5" />
          </button>
        )}

        {/* Vital Signs (Ping/Health) */}
        <div className="no-drag flex items-center gap-2 px-2.5 py-1 bg-[rgba(35,25,14,0.55)] rounded-lg border border-white/5 backdrop-blur-md">
          {/* 当前任务显示 */}
          {(pmRunning || directorRunning) && displayCurrentTask && (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 max-w-[200px]">
              <span className="text-[10px] text-amber-300 truncate" title={displayCurrentTask}>
                {isExecutingTool && displayCurrentToolName ? `工具: ${displayCurrentToolName}` : displayCurrentTask}
              </span>
            </div>
          )}
          {/* 无任务时显示工具执行 */}
          {(pmRunning || directorRunning) && !displayCurrentTask && isExecutingTool && displayCurrentToolName && (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/30 max-w-[200px]">
              <span className="text-[10px] text-cyan-300 truncate" title={displayCurrentToolName}>
                正在执行: {displayCurrentToolName}
              </span>
            </div>
          )}
          {/* 即时状态反馈 */}
          {(pmRunning || directorRunning || currentPhase) && (
            <MiniStatusBadge
              phase={
                isExecutingTool ? 'tool_running' :
                  currentPhase === 'planning' ? 'thinking' :
                    currentPhase === 'implementation' ? 'executing' :
                      pmRunning || directorRunning ? 'executing' : 'idle'
              }
              theme={pmRunning ? 'amber' : 'indigo'}
            />
          )}
          <button
            onClick={onPingHealth}
            className="flex items-center gap-2 group transition-all"
            title="校验连通性"
          >
            <div className={`size-1.5 rounded-full shadow-[0_0_8px_currentColor] transition-colors duration-500 ${healthStatus === 'unhealthy' ? 'bg-status-error text-status-error' : healthStatus ? 'bg-status-success text-status-success' : 'bg-status-warning text-status-warning'}`} />
            <span className="text-[10px] font-mono text-text-dim transition-colors group-hover:text-text-muted uppercase">
              {healthStatus === 'unhealthy' ? UI_TERMS.states.offline : healthStatus ? UI_TERMS.states.ready : UI_TERMS.states.pinging}
            </span>
          </button>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <button
            onClick={onOpenLogs}
            className="flex items-center gap-1.5 text-text-dim hover:text-accent transition-colors"
            title="查看子进程与回执日志"
          >
            <Activity className="size-3.5" />
            <span className="text-[10px] font-bold uppercase tracking-widest">{UI_TERMS.nouns.logs}</span>
          </button>
          {normalizedIoMode || normalizedMemMode ? (
            <>
              <div className="w-px h-3 bg-white/10 mx-1" />
              <div className="flex items-center gap-1">
                {normalizedIoMode ? (
                  <span className={`px-2 py-0.5 rounded border text-[9px] font-mono uppercase tracking-wider ${ioTone}`}>
                    IO:{normalizedIoMode}
                  </span>
                ) : null}
                {normalizedMemMode ? (
                  <span className={`px-2 py-0.5 rounded border text-[9px] font-mono uppercase tracking-wider ${memTone}`}>
                    MEM:{normalizedMemMode}
                  </span>
                ) : null}
              </div>
            </>
          ) : null}
        </div>

        {showAgents ? (
          <div className="no-drag flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg border border-status-warning/30 backdrop-blur-sm">
            <span className="text-[10px] uppercase font-bold text-status-warning tracking-wider px-1">AGENTS</span>
            <button
              onClick={agentsReady ? onOpenAgentsReview : onGenerateAgentsDraft}
              data-testid={agentsReady ? 'control-panel-open-agents-review' : 'control-panel-generate-agents-draft'}
              disabled={agentsReady ? !onOpenAgentsReview : !onGenerateAgentsDraft}
              className={`p-1.5 rounded-md transition-colors relative ${agentsReady
                ? 'bg-status-warning/20 text-status-warning hover:bg-status-warning/30'
                : 'bg-accent/20 text-accent hover:bg-accent/30'
                } ${(!onOpenAgentsReview && agentsReady) || (!onGenerateAgentsDraft && !agentsReady) ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`}
              title={agentsReady ? '打开 AGENTS 审阅' : '生成 AGENTS 草拟稿'}
            >
              <FileText className="size-3.5" />
            </button>
          </div>
        ) : null}

        {onStopOllama ? (
          <div className="no-drag flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg border border-white/5 backdrop-blur-sm">
            <span className="text-[10px] uppercase font-bold text-text-dim tracking-wider px-1">Ollama</span>
            <button
              onClick={onStopOllama}
              disabled={isStoppingOllama}
              className={`p-1.5 rounded-md transition-colors bg-status-error/10 text-status-error hover:bg-status-error/20 relative ${isStoppingOllama ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''
                }`}
              title="停用 Ollama 模型"
            >
              {isStoppingOllama ? <Loader2 className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}
            </button>
          </div>
        ) : null}

        {/* Role Workspace Entries - MOVED TO DROPDOWN */}
        {/* PM Workspace button - moved to more menu */}
        {/* Director Workspace button - moved to more menu */}

        <div className="w-px h-6 bg-white/10 mx-1" />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="btn-icon"
              title="更多功能"
            >
              <MoreHorizontal className="size-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            {onEnterPMWorkspace && (
              <DropdownMenuItem data-testid="enter-pm-workspace" onClick={onEnterPMWorkspace}>
                <Crown className="size-4 mr-2" />
                PM 工作区
              </DropdownMenuItem>
            )}
            {onEnterChiefEngineerWorkspace && (
              <DropdownMenuItem data-testid="enter-chief-engineer-workspace" onClick={onEnterChiefEngineerWorkspace}>
                <ClipboardList className="size-4 mr-2" />
                Chief Engineer 工作区
              </DropdownMenuItem>
            )}
            {onEnterDirectorWorkspace && (
              <DropdownMenuItem data-testid="enter-director-workspace" onClick={onEnterDirectorWorkspace}>
                <Hammer className="size-4 mr-2" />
                Director 工作区
              </DropdownMenuItem>
            )}
            {onEnterAGIWorkspace && (
              <DropdownMenuItem onClick={onEnterAGIWorkspace}>
                <Bot className="size-4 mr-2" />
                AGI 工作区
              </DropdownMenuItem>
            )}
            {onEnterRuntimeDiagnostics && (
              <DropdownMenuItem data-testid="enter-runtime-diagnostics" onClick={onEnterRuntimeDiagnostics}>
                <Gauge className="size-4 mr-2" />
                运行诊断
              </DropdownMenuItem>
            )}
            {showAgents && (
              <DropdownMenuItem onClick={agentsReady ? onOpenAgentsReview : onGenerateAgentsDraft}>
                <FileText className="size-4 mr-2" />
                {agentsReady ? 'AGENTS 审阅' : '生成 AGENTS'}
              </DropdownMenuItem>
            )}
            {onOpenBrain && (
              <DropdownMenuItem onClick={onOpenBrain}>
                <Brain className="size-4 mr-2" />
                明镜台 (Brain)
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => window.dispatchEvent(new CustomEvent('open-intervention-center'))}>
              <ShieldAlert className="size-4 mr-2" />
              干预中心
            </DropdownMenuItem>
            {onStopOllama && (
              <DropdownMenuItem onClick={onStopOllama} disabled={isStoppingOllama}>
                <Square className="size-4 mr-2" />
                {isStoppingOllama ? '停用中...' : '停用 Ollama'}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <button
          className="btn-icon"
          onClick={onRefresh}
        >
          <RefreshCw className="size-4" />
        </button>

        <button
          onClick={onOpenSettings}
          data-testid="control-panel-open-settings"
          className="btn-icon"
          title={UI_TERMS.actions.openSettings}
        >
          <Settings className="size-4" />
        </button>

        {/* Intervention center moved to dropdown menu */}

        <div className="w-px h-6 bg-white/10 mx-1" />

        {/* Brain button moved to dropdown menu */}

        <div className="w-px h-6 bg-white/10 mx-1" />

        <button
          onClick={onToggleTerminal}
          className={`btn-icon group relative ${isTerminalOpen ? 'text-emerald-400 bg-emerald-400/10' : ''}`}
          title="Terminal (Ctrl + `)"
        >
          <TerminalSquare className="size-4" />
        </button>

        <div className="w-px h-6 bg-white/10 mx-1" />
      </div>
    </header>
  );
}

// Helper icon component for ShieldAlert since it might not be imported
function ShieldAlert({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" />
      <path d="M12 8v4" />
      <path d="M12 16h.01" />
    </svg>
  );
}
