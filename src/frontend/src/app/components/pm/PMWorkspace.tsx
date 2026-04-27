import { useState, useCallback, useEffect, useRef } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import {
  Crown,
  ScrollText,
  CheckCircle2,
  MessageSquare,
  Settings,
  ChevronLeft,
  FileText,
  ListTodo,
  History,
  Sparkles,
  BarChart3,
  Loader2,
  Stethoscope,
  Activity,
  Zap,
  Brain,
  FileCode,
  Clock,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { PMTaskPanel } from './PMTaskPanel';
import { PMDocumentPanel } from './PMDocumentPanel';
import { PMAIDialoguePanel } from './PMAIDialoguePanel';
import { PMStatusBar } from './PMStatusBar';
import { PMDiagnosticsPanel } from './PMDiagnosticsPanel';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';

// 阶段到视图的映射
const PHASE_TO_VIEW: Record<string, { view: 'tasks' | 'activity' | 'documents'; icon: React.ReactNode; label: string; color: string }> = {
  'idle': { view: 'tasks', icon: <ListTodo className="w-4 h-4" />, label: '任务', color: 'text-slate-400' },
  'planning': { view: 'tasks', icon: <Brain className="w-4 h-4" />, label: '规划', color: 'text-blue-400' },
  'analyzing': { view: 'activity', icon: <Activity className="w-4 h-4" />, label: '分析', color: 'text-purple-400' },
  'executing': { view: 'activity', icon: <Zap className="w-4 h-4" />, label: '执行', color: 'text-amber-400' },
  'llm_calling': { view: 'activity', icon: <Brain className="w-4 h-4" />, label: '思考', color: 'text-cyan-400' },
  'tool_running': { view: 'activity', icon: <FileCode className="w-4 h-4" />, label: '工具', color: 'text-emerald-400' },
  'verification': { view: 'activity', icon: <CheckCircle2 className="w-4 h-4" />, label: '验证', color: 'text-teal-400' },
  'completed': { view: 'tasks', icon: <CheckCircle2 className="w-4 h-4" />, label: '完成', color: 'text-green-400' },
  'error': { view: 'activity', icon: <Activity className="w-4 h-4" />, label: '错误', color: 'text-red-400' },
};

interface PMWorkspaceProps {
  tasks: PmTask[];
  pmState: Record<string, unknown> | null;
  pmRunning: boolean;
  isStarting?: boolean;
  onBackToMain: () => void;
  onTogglePm: () => void;
  onRunPmOnce: () => void;
  workspace: string;
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  currentPhase?: string;
  factoryMode?: boolean;
}

type PMActiveView = 'tasks' | 'activity' | 'documents' | 'history' | 'analytics';

export function PMWorkspace({
  tasks,
  pmState,
  pmRunning,
  isStarting,
  onBackToMain,
  onTogglePm,
  onRunPmOnce,
  workspace,
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  currentPhase = 'idle',
  factoryMode = false,
}: PMWorkspaceProps) {
  const [activeView, setActiveView] = useState<PMActiveView>('tasks');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedDocumentPath, setSelectedDocumentPath] = useState<string | null>(null);
  const [showAIDialogue, setShowAIDialogue] = useState(true);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  
  // 用户手动切换视图的标记（避免自动切换覆盖用户选择）
  const userSwitchedViewRef = useRef(false);
  const lastPhaseRef = useRef<string>('');
  
  // 自动切换视图基于当前阶段
  useEffect(() => {
    if (!pmRunning || userSwitchedViewRef.current) return;
    
    const phaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
    
    // 只有当阶段真正改变时才切换
    if (currentPhase !== lastPhaseRef.current) {
      lastPhaseRef.current = currentPhase;
      
      // 如果当前视图不是推荐的视图，则自动切换
      if (phaseConfig.view !== activeView) {
        setActiveView(phaseConfig.view);
      }
    }
  }, [currentPhase, pmRunning, activeView]);
  
  // 当用户手动点击导航时，记录用户偏好
  const handleViewChange = useCallback((view: PMActiveView) => {
    userSwitchedViewRef.current = true;
    setActiveView(view);
  }, []);

  const handleTaskSelect = useCallback((taskId: string | null) => {
    userSwitchedViewRef.current = true;
    setSelectedTaskId(taskId);
    setActiveView('tasks');
  }, []);

  const handleDocumentSelect = useCallback((path: string) => {
    userSwitchedViewRef.current = true;
    setSelectedDocumentPath(path);
    setActiveView('documents');
  }, []);

  const completedTasks = tasks.filter(t => t.status === 'completed' || t.done).length;
  const totalTasks = tasks.length;
  const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  
  // 实时任务统计
  const taskStats = {
    pending: tasks.filter(t => !t.status || t.status === 'pending').length,
    running: tasks.filter(t => String(t.status) === 'running' || t.status === 'in_progress').length,
    completed: completedTasks,
    blocked: tasks.filter(t => t.status === 'blocked' || t.status === 'failed').length,
  };
  
  // 获取当前阶段信息
  const currentPhaseConfig = PHASE_TO_VIEW[currentPhase] || PHASE_TO_VIEW['idle'];
  
  // 获取当前正在执行的任务
  const currentTask = tasks.find((task) => task.status === 'in_progress' || String(task.status) === 'running') ?? null;

  return (
    <div data-testid="pm-workspace" className="flex flex-col h-full bg-gradient-to-br from-[var(--ink-indigo)] via-[rgba(28,18,48,0.8)] to-[rgba(14,20,40,0.95)] text-slate-100 overflow-hidden">
      {/* PM Header - PM 主题 */}
      <header className="h-14 flex items-center justify-between px-4 border-b border-amber-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-amber-950/20">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="pm-workspace-back"
            aria-label="返回主界面"
            className="text-slate-400 hover:text-slate-100 hover:bg-white/5"
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            返回
          </Button>

          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center shadow-lg shadow-amber-500/20">
                <Crown className="w-4 h-4 text-amber-100" />
              </div>
              <div className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            </div>
            <div>
              <h1 className="text-sm font-semibold text-amber-100">PM</h1>
              <p className="text-[10px] text-amber-500/70 uppercase tracking-wider">PM Console</p>
            </div>
          </div>
        </div>

        {/* 中央进度指示器 + 当前状态 */}
        <div className="flex items-center gap-4">
          {/* 实时任务统计 - 动画数字 */}
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-white/5 border border-white/10">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs text-slate-400">待办:</span>
            <span className="text-xs font-mono text-slate-300 min-w-[20px] text-center">
              {taskStats.pending}
            </span>
            <span className="text-slate-600">|</span>
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-xs text-amber-400 font-medium min-w-[20px] text-center">
              {taskStats.running}
            </span>
            <span className="text-slate-600">|</span>
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-xs text-emerald-400 font-medium min-w-[20px] text-center">
              {taskStats.completed}
            </span>
            {taskStats.blocked > 0 && (
              <>
                <span className="text-slate-600">|</span>
                <AlertCircle className="w-3.5 h-3.5 text-red-400" />
                <span className="text-xs text-red-400 font-medium min-w-[20px] text-center">
                  {taskStats.blocked}
                </span>
              </>
            )}
          </div>

          {/* 当前阶段状态指示 */}
          {pmRunning && (
            <div className={cn(
              "flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all duration-300",
              currentPhaseConfig.color.replace('text-', 'bg-').replace('400', '500/20'),
              currentPhaseConfig.color
            )}>
              {currentPhaseConfig.icon}
              <span className="text-xs font-medium">{currentPhaseConfig.label}</span>
            </div>
          )}
          
          {/* 任务进度条 */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10">
            <ScrollText className="w-4 h-4 text-amber-500/70" />
            <span className="text-xs text-slate-400">进度</span>
            <span className="text-xs font-mono text-amber-400">
              {completedTasks}/{totalTasks}
            </span>
            <div className="w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-xs font-mono text-slate-500">{progress}%</span>
          </div>
          
          {/* 当前任务指示 - 带脉冲动画 */}
          {currentTask && pmRunning && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 max-w-[250px] animate-pulse">
              <Zap className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 animate-pulse" />
              <span className="text-xs text-amber-300 truncate" title={currentTask.title}>
                正在执行: {currentTask.title}
              </span>
            </div>
          )}
        </div>

        {/* 右侧控制 */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowDiagnostics(true)}
            className="text-slate-400 hover:text-amber-400 hover:bg-amber-500/10"
            title="运行诊断"
          >
            <Stethoscope className="w-4 h-4" />
          </Button>

          <div className="w-px h-6 bg-white/10" />

          <Button
            variant="ghost"
            size="sm"
            onClick={onRunPmOnce}
            data-testid="pm-workspace-run-once"
            disabled={pmRunning || isStarting || factoryMode}
            title={factoryMode ? "工厂模式下无法使用此功能" : undefined}
            className="text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 border border-amber-500/20"
          >
            {isStarting ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 mr-1.5" />}
            单次 Run
          </Button>

          <Button
            variant={pmRunning ? 'default' : 'outline'}
            size="sm"
            onClick={onTogglePm}
            data-testid="pm-workspace-toggle"
            disabled={isStarting || factoryMode}
            title={factoryMode ? "工厂模式下无法使用此功能" : undefined}
            className={cn(
              pmRunning
                ? 'bg-amber-600 hover:bg-amber-700 text-white'
                : 'border-amber-500/30 text-amber-400 hover:bg-amber-500/10'
            )}
          >
            {isStarting ? (
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
            ) : pmRunning ? (
              <>
                <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse mr-2" />
                运行中
              </>
            ) : (
              <>
                <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />
                启动
              </>
            )}
          </Button>

          <div className="w-px h-6 bg-white/10 mx-2" />

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowAIDialogue(!showAIDialogue)}
            className={cn(
              'text-slate-400 hover:text-slate-100',
              showAIDialogue && 'text-amber-400 bg-amber-500/10'
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

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - Navigation */}
        <nav className="w-14 flex flex-col items-center py-4 gap-2 border-r border-white/5 bg-slate-950/50">
          <NavButton
            icon={<ListTodo className="w-4 h-4" />}
            label="任务"
            active={activeView === 'tasks'}
            onClick={() => handleViewChange('tasks')}
          />
          <NavButton
            icon={<Activity className="w-4 h-4" />}
            label="实时"
            active={activeView === 'activity'}
            onClick={() => handleViewChange('activity')}
          />
          <NavButton
            icon={<FileText className="w-4 h-4" />}
            label="文档"
            active={activeView === 'documents'}
            onClick={() => handleViewChange('documents')}
          />
          <NavButton
            icon={<History className="w-4 h-4" />}
            label="历史"
            active={activeView === 'history'}
            onClick={() => handleViewChange('history')}
          />
          <NavButton
            icon={<BarChart3 className="w-4 h-4" />}
            label="统计"
            active={activeView === 'analytics'}
            onClick={() => setActiveView('analytics')}
          />
        </nav>

        {/* Main Panel */}
        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={showAIDialogue ? 65 : 85} minSize={40}>
            <div className="h-full overflow-hidden">
              {activeView === 'tasks' && (
                <PMTaskPanel
                  tasks={tasks}
                  selectedTaskId={selectedTaskId}
                  onTaskSelect={handleTaskSelect}
                  pmRunning={pmRunning}
                />
              )}
              {activeView === 'activity' && (
                <RealtimeActivityPanel
                  executionLogs={executionLogs}
                  llmStreamEvents={llmStreamEvents}
                  processStreamEvents={processStreamEvents}
                  currentPhase={currentPhase}
                  isRunning={pmRunning}
                  role="pm"
                />
              )}
              {activeView === 'documents' && (
                <PMDocumentPanel
                  workspace={workspace}
                  selectedPath={selectedDocumentPath}
                  onDocumentSelect={handleDocumentSelect}
                />
              )}
              {activeView === 'history' && (
                <PMHistoryPanel pmState={pmState} />
              )}
              {activeView === 'analytics' && (
                <PMAnalyticsPanel tasks={tasks} />
              )}
            </div>
          </Panel>

          {showAIDialogue && (
            <>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-amber-500/30 transition-colors" />
              <Panel defaultSize={35} minSize={25} maxSize={50}>
                <PMAIDialoguePanel
                  pmRunning={pmRunning}
                  workspace={workspace}
                  taskCount={totalTasks}
                />
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>

      {/* Status Bar */}
      <PMStatusBar
        pmRunning={pmRunning}
        taskCount={totalTasks}
        completedCount={completedTasks}
        iteration={pmState?.pm_iteration as number | undefined}
      />

      {/* Diagnostics Panel */}
      <PMDiagnosticsPanel
        isOpen={showDiagnostics}
        onClose={() => setShowDiagnostics(false)}
      />
    </div>
  );
}

// Navigation Button Component
interface NavButtonProps {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}

function NavButton({ icon, label, active, onClick }: NavButtonProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-10 h-10 rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200',
        active
          ? 'bg-amber-500/15 text-amber-400 shadow-lg shadow-amber-500/10'
          : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
      )}
      title={label}
    >
      {icon}
      <span className="text-[8px] font-medium">{label}</span>
    </button>
  );
}

// Placeholder Components (will be implemented in separate files)
function PMHistoryPanel({ pmState }: { pmState: Record<string, unknown> | null }) {
  return (
    <div className="h-full flex flex-col p-6">
      <h2 className="text-lg font-semibold text-slate-100 mb-4">执行历史</h2>
      <div className="flex-1 rounded-xl border border-white/10 bg-white/5 p-4 overflow-auto">
        <pre className="text-xs text-slate-400 font-mono">
          {JSON.stringify(pmState, null, 2) || '暂无历史记录'}
        </pre>
      </div>
    </div>
  );
}

function PMAnalyticsPanel({ tasks }: { tasks: PmTask[] }) {
  const statusCounts = tasks.reduce((acc, task) => {
    const status = task.status || 'unknown';
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div className="h-full flex flex-col p-6">
      <h2 className="text-lg font-semibold text-slate-100 mb-4">任务统计</h2>
      <div className="grid grid-cols-2 gap-4">
        {Object.entries(statusCounts).map(([status, count]) => (
          <div
            key={status}
            className="p-4 rounded-xl border border-white/10 bg-white/5"
          >
            <p className="text-xs text-slate-500 uppercase">{status}</p>
            <p className="text-2xl font-bold text-amber-400">{count}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
