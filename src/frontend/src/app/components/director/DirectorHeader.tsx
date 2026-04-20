/**
 * DirectorHeader - Director工作区头部组件
 */
import {
  Hammer,
  ChevronLeft,
  Clock,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Activity,
  Play,
  Pause,
  RotateCcw,
  MessageSquare,
  Settings,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type { ExecutionSession } from './hooks/useDirectorWorkspace';

interface DirectorHeaderProps {
  sessionStatus: ExecutionSession['status'];
  sessionId: string;
  workspace: string;
  directorRunning: boolean;
  isStarting: boolean;
  currentTaskTitle?: string | null;
  runningTasks: number;
  completedTasks: number;
  failedTasks: number;
  pendingTasks: number;
  totalTasks: number;
  progress: number;
  showAIDialogue: boolean;
  factoryMode?: boolean;
  onBackToMain: () => void;
  onToggleDirector: () => void;
  onPause: () => void;
  onReset: () => void;
  onToggleAIDialogue: () => void;
}

export function DirectorHeader({
  sessionStatus,
  sessionId,
  workspace,
  directorRunning,
  isStarting,
  currentTaskTitle,
  runningTasks,
  completedTasks,
  failedTasks,
  pendingTasks,
  totalTasks,
  progress,
  showAIDialogue,
  factoryMode = false,
  onBackToMain,
  onToggleDirector,
  onPause,
  onReset,
  onToggleAIDialogue,
}: DirectorHeaderProps) {
  return (
    <header className="h-14 flex items-center justify-between px-4 border-b border-indigo-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/20">
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBackToMain}
          data-testid="director-workspace-back"
          className="text-slate-400 hover:text-slate-100 hover:bg-white/5"
        >
          <ChevronLeft className="w-4 h-4 mr-1" />
          返回
        </Button>

        <div className="flex items-center gap-3">
          <div className="relative">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-700 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Hammer className="w-4 h-4 text-indigo-100" />
            </div>
            {sessionStatus === 'running' && (
              <div className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse" />
            )}
          </div>
          <div>
            <h1 className="text-sm font-semibold text-indigo-100">工部侍郎</h1>
            <p className="text-[10px] text-indigo-500/70 uppercase tracking-wider">Director Console</p>
          </div>
        </div>
      </div>

      {/* 中央执行状态 */}
      <div className="flex items-center gap-4">
        {/* 实时任务统计 */}
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10">
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-xs text-slate-400">待定:</span>
          <span className="text-xs font-mono text-slate-300 min-w-[20px] text-center">
            {pendingTasks}
          </span>
          <span className="text-slate-600">|</span>
          <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />
          <span className="text-xs text-blue-400 font-medium min-w-[20px] text-center">
            {runningTasks}
          </span>
          <span className="text-slate-600">|</span>
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
          <span className="text-xs text-emerald-400 font-medium min-w-[20px] text-center">
            {completedTasks}
          </span>
          {failedTasks > 0 && (
            <>
              <span className="text-slate-600">|</span>
              <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
              <span className="text-xs text-red-400 font-medium min-w-[20px] text-center">
                {failedTasks}
              </span>
            </>
          )}
        </div>

        {/* 进度条 */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10">
          <Activity className="w-4 h-4 text-indigo-500/70" />
          <span className="text-xs text-slate-400">进度</span>
          <span className="text-xs font-mono text-indigo-400">
            {completedTasks}/{totalTasks}
          </span>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <div className="w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-400 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="text-xs font-mono text-slate-500">{progress}%</span>
        </div>

        {/* 当前执行任务 - 实时显示 */}
        {currentTaskTitle && directorRunning && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 max-w-[250px] animate-pulse">
            <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin flex-shrink-0" />
            <span className="text-xs text-indigo-300 truncate" title={currentTaskTitle || ''}>
              正在执行: {currentTaskTitle}
            </span>
          </div>
        )}

        {failedTasks > 0 && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/20">
            <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
            <span className="text-xs text-red-400">{failedTasks} 失败</span>
          </div>
        )}
      </div>

      {/* 右侧控制 */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onToggleDirector}
          data-testid="director-workspace-execute"
          disabled={factoryMode}
          title={factoryMode ? "工厂模式下无法使用此功能" : undefined}
          className="border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
        >
          {isStarting ? (
            <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          ) : (
            <Play className="w-3.5 h-3.5 mr-1.5" />
          )}
          {directorRunning ? '停止' : '执行'}
        </Button>

        <Button
          variant="ghost"
          size="icon"
          onClick={onPause}
          data-testid="director-workspace-pause"
          disabled={!directorRunning}
          className="text-slate-400 hover:text-indigo-400 hover:bg-indigo-500/10"
        >
          <Pause className="w-4 h-4" />
        </Button>

        <Button
          variant="ghost"
          size="icon"
          onClick={onReset}
          data-testid="director-workspace-reset"
          className="text-slate-400 hover:text-slate-100"
        >
          <RotateCcw className="w-4 h-4" />
        </Button>

        <div className="w-px h-6 bg-white/10 mx-2" />

        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleAIDialogue}
          className={cn(
            'text-slate-400 hover:text-slate-100',
            showAIDialogue && 'text-indigo-400 bg-indigo-500/10'
          )}
        >
          <MessageSquare className="w-4 h-4" />
        </Button>

        <Button
          variant="ghost"
          size="icon"
          className="text-slate-400 hover:text-slate-100"
        >
          <Settings className="w-4 h-4" />
        </Button>
      </div>
    </header>
  );
}
