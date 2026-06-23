import { useState } from 'react';
import { Anchor, Play, Square, Settings, FolderOpen, RefreshCw, Zap, Loader2, FastForward, FileText, Brain, Activity, TerminalSquare, Crown, Hammer, MoreHorizontal, Bot, ClipboardList, Gauge, ShieldAlert, Network, Files } from 'lucide-react';
import { WindowControls } from './WindowControls';
import { UsageHUD, type UsageStats } from './UsageHUD';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { MiniStatusBadge } from '@/app/components/ai-dialogue/ManusStyleStatusIndicator';
import { cleanRuntimeDisplayText } from '@/app/utils/runtimeDisplay';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
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
  pmBlockedReason?: string;
  directorToggleDisabled?: boolean;
  directorBlockedReason?: string;
  runOnceDisabled?: boolean;
  runOnceBlockedReason?: string;
  agentsNeeded?: boolean;
  agentsDraftReady?: boolean;
  agentsDraftFailed?: boolean;
  onOpenAgentsReview?: () => void;
  onGenerateAgentsDraft?: () => void;
  onOpenSettings: () => void;
  onPickWorkspace?: () => void;
  onTogglePm: () => void | boolean | Promise<void | boolean>;
  onRunPmOnce?: () => void | boolean | Promise<void | boolean>;
  onResumePm?: () => void | boolean | Promise<void | boolean>;
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
  onStopOllama?: () => void;
  onRefresh: () => void;
  onOpenBrain?: () => void;
  onEnterPMWorkspace?: () => void;
  onEnterChiefEngineerWorkspace?: () => void;
  onEnterDirectorWorkspace?: () => void;
  onEnterFactoryMode?: () => void;
  onEnterAGIWorkspace?: () => void;
  onEnterRuntimeDiagnostics?: () => void;
  onEnterContextOS?: () => void;
  onEnterFiles?: () => void;
  onOpenIntervention?: () => void;
  workspaceError?: string | null;
  isStartingPM?: boolean;
  isStoppingPM?: boolean;
  isStartingDirector?: boolean;
  isStoppingDirector?: boolean;
  isStoppingOllama?: boolean;
  healthStatus?: string | null;
  healthStatusDetail?: string | null;
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

interface ProcessCommandEvidence {
  triggered: boolean;
  loading: boolean;
  message: string | null;
  error: string | null;
}

const RUNTIME_PUSH_ENDPOINT = '/v2/ws/runtime';
const RUNTIME_PUSH_WAITING_MESSAGE = 'command submitted · waiting runtime.v2 push';

function processEvidenceText(evidence: ProcessCommandEvidence): string {
  if (evidence.loading) {
    return `${RUNTIME_PUSH_ENDPOINT} · submitting command`;
  }
  if (evidence.error) {
    return `${RUNTIME_PUSH_ENDPOINT} · ${evidence.error}`;
  }
  return `${RUNTIME_PUSH_ENDPOINT} · ${evidence.message || RUNTIME_PUSH_WAITING_MESSAGE}`;
}

function processEvidenceSummary(evidence: ProcessCommandEvidence): string {
  if (evidence.loading) {
    return 'submitting';
  }
  if (evidence.error) {
    return evidence.error;
  }
  return evidence.message || 'waiting runtime.v2';
}

export function ControlPanel({
  workspace,
  pmRunning,
  directorRunning,
  pmToggleDisabled,
  pmBlockedReason,
  directorToggleDisabled,
  directorBlockedReason,
  runOnceDisabled,
  runOnceBlockedReason,
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
  onEnterContextOS,
  onEnterFiles,
  onOpenIntervention,
  workspaceError,
  isStartingPM,
  isStoppingPM,
  isStartingDirector,
  isStoppingDirector,
  isStoppingOllama,
  healthStatus,
  healthStatusDetail,
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
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const [pmToggleEvidence, setPmToggleEvidence] = useState<ProcessCommandEvidence>({
    triggered: false,
    loading: false,
    message: null,
    error: null,
  });
  const [directorToggleEvidence, setDirectorToggleEvidence] = useState<ProcessCommandEvidence>({
    triggered: false,
    loading: false,
    message: null,
    error: null,
  });
  const pmDisabled = !!pmToggleDisabled;
  const directorDisabled = !!directorToggleDisabled;
  const runOnceBlocked = !!runOnceDisabled;
  const pmDisabledTitle = pmDisabled && pmBlockedReason
    ? pmBlockedReason
    : pmRunning
      ? UI_TERMS.actions.stopLoop
      : UI_TERMS.actions.startLoop;
  const runOnceTitle = runOnceBlocked && runOnceBlockedReason ? runOnceBlockedReason : UI_TERMS.actions.runOnce;
  const pmToggleBusy = Boolean(isStartingPM || isStoppingPM || pmToggleEvidence.loading);
  const directorToggleBusy = Boolean(isStartingDirector || isStoppingDirector || directorToggleEvidence.loading);
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
  const displayWorkspace = workspaceLabel(workspace, '');
  const healthTone = healthStatus === 'unhealthy'
    ? 'bg-status-error text-status-error'
    : healthStatus === 'healthy' || healthStatus === 'ok'
      ? 'bg-status-success text-status-success'
      : 'bg-status-warning text-status-warning';
  const healthLabel = healthStatus === 'unhealthy'
    ? UI_TERMS.states.offline
    : healthStatus === 'checking'
      ? UI_TERMS.states.pinging
      : healthStatus
        ? UI_TERMS.states.ready
        : UI_TERMS.states.pinging;

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
  const runMoreMenuAction = (action?: () => void | boolean | Promise<void | boolean>) => {
    setMoreMenuOpen(false);
    void action?.();
  };
  const openInterventionCenter = () => {
    if (onOpenIntervention) {
      onOpenIntervention();
      return;
    }
    window.dispatchEvent(new CustomEvent('open-intervention-center'));
  };

  const handleTogglePm = async () => {
    setPmToggleEvidence({
      triggered: true,
      loading: true,
      message: null,
      error: null,
    });
    try {
      const accepted = await Promise.resolve(onTogglePm());
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
        error: accepted === false ? 'PM command was not accepted' : null,
      });
    } catch (error) {
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: null,
        error: error instanceof Error ? error.message : 'PM status unavailable',
      });
    }
  };

  const handleRunPmOnce = async () => {
    if (!onRunPmOnce) {
      return;
    }
    setPmToggleEvidence({
      triggered: true,
      loading: true,
      message: null,
      error: null,
    });
    try {
      const accepted = await Promise.resolve(onRunPmOnce());
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
        error: accepted === false ? 'PM command was not accepted' : null,
      });
    } catch (error) {
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: null,
        error: error instanceof Error ? error.message : 'PM status unavailable',
      });
    }
  };

  const handleResumePm = async () => {
    if (!onResumePm) {
      return;
    }
    setPmToggleEvidence({
      triggered: true,
      loading: true,
      message: null,
      error: null,
    });
    try {
      const accepted = await Promise.resolve(onResumePm());
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
        error: accepted === false ? 'PM command was not accepted' : null,
      });
    } catch (error) {
      setPmToggleEvidence({
        triggered: true,
        loading: false,
        message: null,
        error: error instanceof Error ? error.message : 'PM status unavailable',
      });
    }
  };

  const handleToggleDirector = async () => {
    setDirectorToggleEvidence({
      triggered: true,
      loading: true,
      message: null,
      error: null,
    });
    try {
      const accepted = await Promise.resolve(onToggleDirector());
      setDirectorToggleEvidence({
        triggered: true,
        loading: false,
        message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
        error: accepted === false ? 'Director command was not accepted' : null,
      });
    } catch (error) {
      setDirectorToggleEvidence({
        triggered: true,
        loading: false,
        message: null,
        error: error instanceof Error ? error.message : 'Director status unavailable',
      });
    }
  };

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
              data-testid="control-panel-toggle-monitor"
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
      <div className="flex-1 max-w-[26rem] mx-6 relative group">
        <div
          className={`no-drag flex items-center gap-2 soft-inset rounded-lg px-3 py-1.5 transition-all duration-300 ${workspaceError ? 'border-status-error/60' : 'group-hover:border-accent/30'
            }`}
          title={workspaceError || workspace || undefined}
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
            data-testid="control-panel-workspace-label"
            value={displayWorkspace}
            readOnly
            title={workspace || undefined}
            data-workspace-path={workspace || undefined}
            className="min-w-0 flex-1 bg-transparent text-sm text-text-main outline-none font-sans placeholder:text-text-dim/50 cursor-default"
            placeholder={`请点击左侧按钮选定${UI_TERMS.nouns.workspace}（Workspace）...`}
            aria-invalid={workspaceError ? true : undefined}
            aria-describedby={workspaceError ? 'workspace-error' : undefined}
          />
        </div>
        {workspaceError ? (
          <div
            id="workspace-error"
            className="absolute left-0 right-0 top-full mt-1 text-xs text-status-error bg-bg-panel border border-status-error/30 rounded px-2 py-1 shadow-md z-50"
          >
            {workspaceError}
          </div>
        ) : null}
      </div>

      {/* 控制按钮 */}
      <div className="flex items-center gap-3">
        {/* PM 控制 */}
        <div className="no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg">
          <span className="text-[10px] uppercase font-bold text-text-dim tracking-wider px-1">{UI_TERMS.roles.pm}</span>
          {pmBlockedReason ? (
            <span
              className="max-w-[170px] truncate rounded border border-status-error/20 bg-status-error/20 px-1.5 py-0.5 text-[10px] text-status-error"
              title={pmBlockedReason}
            >
              {pmBlockedReason}
            </span>
          ) : null}
          <button
            onClick={() => { void handleTogglePm(); }}
            data-testid="control-panel-pm-toggle"
            disabled={pmDisabled || pmToggleBusy}
            className={`p-1.5 rounded-md transition-all duration-300 relative ${pmRunning
              ? 'soft-raised text-accent'
              : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'
              } ${pmDisabled || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`}
            title={pmDisabledTitle}
          >
            {pmToggleBusy ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : pmRunning ? (
              <Square className="size-3.5 fill-current" />
            ) : (
              <Play className="size-3.5 fill-current" />
            )}
          </button>
          {pmToggleEvidence.triggered ? (
            <span
              data-testid="control-panel-pm-toggle-evidence"
              title={processEvidenceText(pmToggleEvidence)}
              data-endpoint={RUNTIME_PUSH_ENDPOINT}
              data-evidence={processEvidenceText(pmToggleEvidence)}
              className={`max-w-[170px] truncate rounded border px-1.5 py-0.5 font-mono text-[10px] ${pmToggleEvidence.error
                ? 'border-status-error/30 bg-status-error/10 text-status-error'
                : pmToggleEvidence.loading
                  ? 'border-white/10 bg-white/5 text-text-muted'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                }`}
            >
              {processEvidenceSummary(pmToggleEvidence)}
            </span>
          ) : null}
          {onRunPmOnce ? (
            <button
              onClick={() => { void handleRunPmOnce(); }}
              data-testid="control-panel-pm-run-once"
              disabled={runOnceBlocked || pmToggleBusy}
              className={`p-1.5 rounded-md transition-colors text-text-muted hover:text-accent hover:bg-accent-dim relative ${runOnceBlocked || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''
                }`}
              title={runOnceTitle}
            >
              {pmToggleBusy ? <Loader2 className="size-3.5 animate-spin" /> : <Zap className="size-3.5" />}
            </button>
          ) : null}
          {onResumePm && !pmRunning ? (
            <button
              onClick={() => { void handleResumePm(); }}
              disabled={pmDisabled || pmToggleBusy}
              className={`p-1.5 rounded-md transition-colors text-text-muted hover:text-status-warning hover:bg-status-warning/10 relative ${pmDisabled || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''
                }`}
              title={UI_TERMS.actions.resumeLast}
            >
              <FastForward className="size-3.5" />
            </button>
          ) : null}
        </div>

        {/* Director 控制 */}
        <div className="no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg">
          <span className="text-[10px] uppercase font-bold text-text-dim tracking-wider px-1">{UI_TERMS.roles.director}</span>
          {directorBlockedReason ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-status-error/20 text-status-error border border-status-error/20">
              {directorBlockedReason}
            </span>
          ) : null}
          <button
            onClick={() => { void handleToggleDirector(); }}
            data-testid="control-panel-director-toggle"
            disabled={directorDisabled || directorToggleBusy}
            className={`p-1.5 rounded-md transition-all duration-300 relative ${directorRunning
              ? 'soft-raised text-accent'
              : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'
              } ${directorDisabled || directorToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`}
            title={directorBlockedReason || undefined}
          >
            {directorToggleBusy ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : directorRunning ? (
              <Square className="size-3.5 fill-current" />
            ) : (
              <Play className="size-3.5 fill-current" />
            )}
          </button>
          {directorToggleEvidence.triggered ? (
            <span
              data-testid="control-panel-director-toggle-evidence"
              title={processEvidenceText(directorToggleEvidence)}
              data-endpoint={RUNTIME_PUSH_ENDPOINT}
              data-evidence={processEvidenceText(directorToggleEvidence)}
              className={`max-w-[190px] truncate rounded border px-1.5 py-0.5 font-mono text-[10px] ${directorToggleEvidence.error
                ? 'border-status-error/30 bg-status-error/10 text-status-error'
                : directorToggleEvidence.loading
                  ? 'border-white/10 bg-white/5 text-text-muted'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                }`}
            >
              {processEvidenceSummary(directorToggleEvidence)}
            </span>
          ) : null}
        </div>

        {/* Factory 模式按钮 */}
        {onEnterFactoryMode && (
          <button
            onClick={onEnterFactoryMode}
            data-testid="control-panel-enter-factory"
            className="no-drag p-1.5 rounded-md transition-all soft-raised text-emerald-400"
            title="Factory 模式 - 无人值守开发工厂"
          >
            <Hammer className="size-3.5" />
          </button>
        )}

        {/* ContextOS 实时视图入口 */}
        {onEnterContextOS && (
          <button
            onClick={onEnterContextOS}
            data-testid="control-panel-enter-contextos"
            className="no-drag flex items-center gap-1.5 px-2.5 py-1.5 rounded-md transition-all soft-chip text-text-muted hover:text-text-main"
            title="ContextOS 实时视图 - 上下文操作系统数据流监控"
          >
            <Network className="size-3.5" />
            <span className="text-[10px] font-bold uppercase tracking-wider">ContextOS</span>
          </button>
        )}

        {/* Workspace file browser entry */}
        {onEnterFiles && (
          <button
            onClick={onEnterFiles}
            data-testid="control-panel-enter-files"
            className="no-drag flex items-center gap-1.5 px-2.5 py-1.5 rounded-md transition-all soft-chip text-text-muted hover:text-text-main"
            title="Workspace 文件浏览器 - 浏览项目文件并预览代码"
          >
            <Files className="size-3.5" />
            <span className="text-[10px] font-bold uppercase tracking-wider">Files</span>
          </button>
        )}

        {/* Vital Signs (Ping/Health) */}
        <div className="no-drag flex items-center gap-2 px-2.5 py-1 soft-panel-subtle rounded-lg">
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
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 max-w-[200px]">
              <span className="text-[10px] text-amber-300 truncate" title={displayCurrentToolName}>
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
            className="group flex shrink-0 items-center gap-2 whitespace-nowrap transition-all"
            title={healthStatusDetail || '校验连通性'}
            data-testid="control-panel-health-ping"
          >
            <div className={`size-1.5 rounded-full transition-colors duration-500 ${healthTone}`} />
            <span className="whitespace-nowrap text-[10px] font-mono uppercase text-text-dim transition-colors group-hover:text-text-muted">
              {healthLabel}
            </span>
          </button>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <button
            onClick={onOpenLogs}
            data-testid="control-panel-open-logs"
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
          <div className="no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg border border-status-warning/30">
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
          <div className="no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg">
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

        <DropdownMenu open={moreMenuOpen} onOpenChange={setMoreMenuOpen}>
          <DropdownMenuTrigger asChild>
            <button
              className="btn-icon"
              data-testid="control-panel-more-menu"
              title="更多功能"
            >
              <MoreHorizontal className="size-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            {onEnterPMWorkspace && (
              <DropdownMenuItem data-testid="enter-pm-workspace" onClick={() => runMoreMenuAction(onEnterPMWorkspace)}>
                <Crown className="size-4 mr-2" />
                PM 工作区
              </DropdownMenuItem>
            )}
            {onEnterChiefEngineerWorkspace && (
              <DropdownMenuItem data-testid="enter-chief-engineer-workspace" onClick={() => runMoreMenuAction(onEnterChiefEngineerWorkspace)}>
                <ClipboardList className="size-4 mr-2" />
                Chief Engineer 工作区
              </DropdownMenuItem>
            )}
            {onEnterDirectorWorkspace && (
              <DropdownMenuItem data-testid="enter-director-workspace" onClick={() => runMoreMenuAction(onEnterDirectorWorkspace)}>
                <Hammer className="size-4 mr-2" />
                Director 工作区
              </DropdownMenuItem>
            )}
            {onEnterAGIWorkspace && (
              <DropdownMenuItem data-testid="enter-agi-workspace" onClick={() => runMoreMenuAction(onEnterAGIWorkspace)}>
                <Bot className="size-4 mr-2" />
                AGI 工作区
              </DropdownMenuItem>
            )}
            {onEnterRuntimeDiagnostics && (
              <DropdownMenuItem data-testid="enter-runtime-diagnostics" onClick={() => runMoreMenuAction(onEnterRuntimeDiagnostics)}>
                <Gauge className="size-4 mr-2" />
                运行诊断
              </DropdownMenuItem>
            )}
            {onEnterContextOS && (
              <DropdownMenuItem data-testid="enter-contextos-menu-item" onClick={() => runMoreMenuAction(onEnterContextOS)}>
                <Network className="size-4 mr-2" />
                ContextOS 实时视图
              </DropdownMenuItem>
            )}
            {onEnterFiles && (
              <DropdownMenuItem data-testid="enter-files-menu-item" onClick={() => runMoreMenuAction(onEnterFiles)}>
                <Files className="size-4 mr-2" />
                Workspace 文件浏览器
              </DropdownMenuItem>
            )}
            {showAgents && (
              <DropdownMenuItem data-testid={agentsReady ? 'open-agents-review-menu-item' : 'generate-agents-menu-item'} onClick={() => runMoreMenuAction(agentsReady ? onOpenAgentsReview : onGenerateAgentsDraft)}>
                <FileText className="size-4 mr-2" />
                {agentsReady ? 'AGENTS 审阅' : '生成 AGENTS'}
              </DropdownMenuItem>
            )}
            {onOpenBrain && (
              <DropdownMenuItem data-testid="open-brain-menu-item" onClick={() => runMoreMenuAction(onOpenBrain)}>
                <Brain className="size-4 mr-2" />
                明镜台 (Brain)
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem data-testid="open-intervention-center-menu-item" onClick={() => runMoreMenuAction(openInterventionCenter)}>
              <ShieldAlert className="size-4 mr-2" />
              干预中心
            </DropdownMenuItem>
            {onStopOllama && (
              <DropdownMenuItem onClick={() => runMoreMenuAction(onStopOllama)} disabled={isStoppingOllama}>
                <Square className="size-4 mr-2" />
                {isStoppingOllama ? '停用中...' : '停用 Ollama'}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <button
          className="btn-icon"
          onClick={onRefresh}
          data-testid="control-panel-refresh"
          title="刷新运行状态"
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
          data-testid="control-panel-toggle-terminal"
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
