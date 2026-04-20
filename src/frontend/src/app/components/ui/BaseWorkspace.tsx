/** BaseWorkspace - 统一工作区基础组件
 *
 * 提供所有工作区（PM/Director/Factory）的通用功能：
 * - Header 导航
 * - 左侧边栏导航
 * - 视图切换
 * - 状态指示器
 * - 返回按钮
 */
import { useState, ReactNode } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { 
  ChevronLeft, 
  ListTodo,
  Activity,
  FileCode,
  Terminal,
  Bug,
  FileText,
  History,
  BarChart3,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { MiniStatusBadge } from '@/app/components/ai-dialogue/ManusStyleStatusIndicator';

export type ViewType = 'tasks' | 'activity' | 'documents' | 'history' | 'analytics' | 'code' | 'terminal' | 'debug';

export interface NavItem {
  id: ViewType;
  label: string;
  icon: ReactNode;
}

export interface BaseWorkspaceProps {
  /** 工作区标题 */
  title: string;
  /** 副标题 */
  subtitle?: string;
  /** 主题色 */
  theme: 'amber' | 'indigo' | 'emerald';
  /** 是否运行中 */
  isRunning?: boolean;
  /** 当前阶段 */
  currentPhase?: string;
  /** 当前任务 */
  currentTask?: string;
  /** 是否正在执行工具 */
  isExecutingTool?: boolean;
  /** 当前工具名称 */
  currentToolName?: string;
  /** 返回回调 */
  onBack: () => void;
  /** 导航项 */
  navItems: NavItem[];
  /** 当前激活的视图 */
  activeView: ViewType;
  /** 视图切换回调 */
  onViewChange: (view: ViewType) => void;
  /** 主面板内容 */
  children: ReactNode;
  /** 右侧面板（可选） */
  rightPanel?: ReactNode;
  /** 右侧面板默认宽度 */
  rightPanelSize?: number;
  /** 是否显示右侧面板 */
  showRightPanel?: boolean;
  /** 切换右侧面板显示 */
  onToggleRightPanel?: () => void;
}

/** 主题配置 */
const THEME_CONFIG = {
  amber: {
    border: 'border-amber-500/20',
    bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-amber-950/20',
    active: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
    idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
  },
  indigo: {
    border: 'border-indigo-500/20',
    bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/20',
    active: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
    idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
  },
  emerald: {
    border: 'border-emerald-500/20',
    bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-emerald-950/20',
    active: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
  },
};

export function BaseWorkspace({
  title,
  subtitle,
  theme,
  isRunning = false,
  currentPhase,
  isExecutingTool,
  onBack,
  navItems,
  activeView,
  onViewChange,
  children,
  rightPanel,
  rightPanelSize = 35,
  showRightPanel = true,
}: BaseWorkspaceProps) {
  const config = THEME_CONFIG[theme];

  return (
    <div className="h-screen flex flex-col bg-slate-950">
      {/* Header */}
      <header className={cn(
        'h-14 flex items-center justify-between px-4 border-b',
        config.border,
        config.bgGradient
      )}>
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
          >
            <ChevronLeft className="w-4 h-4 text-slate-400" />
          </button>
          <div className="w-px h-6 bg-white/10" />
          <div>
            <div className="text-sm font-semibold text-slate-200">{title}</div>
            {subtitle && (
              <div className="text-[10px] text-slate-500">{subtitle}</div>
            )}
          </div>
        </div>

        {/* 状态指示器 */}
        <div className="flex items-center gap-3">
          {(isRunning || currentPhase) && (
            <MiniStatusBadge
              phase={
                isExecutingTool ? 'tool_running' :
                currentPhase === 'planning' ? 'thinking' :
                currentPhase === 'implementation' ? 'executing' :
                isRunning ? 'executing' : 'idle'
              }
              theme={theme}
            />
          )}
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - Navigation */}
        <nav className="w-14 flex flex-col items-center py-4 gap-2 border-r border-white/5 bg-slate-950/50">
          {navItems.map((item) => (
            <NavButton
              key={item.id}
              icon={item.icon}
              label={item.label}
              active={activeView === item.id}
              onClick={() => onViewChange(item.id)}
              theme={theme}
            />
          ))}
        </nav>

        {/* Main Panel */}
        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={showRightPanel && rightPanel ? 100 - rightPanelSize : 100} minSize={40}>
            <div className="h-full overflow-hidden">
              {children}
            </div>
          </Panel>

          {rightPanel && showRightPanel && (
            <>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-white/10 transition-colors" />
              <Panel defaultSize={rightPanelSize} minSize={25} maxSize={50}>
                {rightPanel}
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>
    </div>
  );
}

/** 导航按钮组件 */
function NavButton({
  icon,
  label,
  active,
  onClick,
  theme,
}: {
  icon: ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  theme: 'amber' | 'indigo' | 'emerald';
}) {
  const config = THEME_CONFIG[theme];
  
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-10 h-10 rounded-lg flex flex-col items-center justify-center gap-0.5 transition-all border',
        active 
          ? config.active 
          : config.idle
      )}
      title={label}
    >
      {icon}
      <span className="text-[8px]">{label}</span>
    </button>
  );
}

/** 预设导航项 */
export const DEFAULT_NAV_ITEMS: Record<string, NavItem[]> = {
  pm: [
    { id: 'tasks', label: '任务', icon: <ListTodo className="w-4 h-4" /> },
    { id: 'activity', label: '实时', icon: <Activity className="w-4 h-4" /> },
    { id: 'documents', label: '文档', icon: <FileText className="w-4 h-4" /> },
    { id: 'history', label: '历史', icon: <History className="w-4 h-4" /> },
    { id: 'analytics', label: '统计', icon: <BarChart3 className="w-4 h-4" /> },
  ],
  director: [
    { id: 'tasks', label: '任务', icon: <ListTodo className="w-4 h-4" /> },
    { id: 'activity', label: '实时', icon: <Activity className="w-4 h-4" /> },
    { id: 'code', label: '代码', icon: <FileCode className="w-4 h-4" /> },
    { id: 'terminal', label: '终端', icon: <Terminal className="w-4 h-4" /> },
    { id: 'debug', label: '调试', icon: <Bug className="w-4 h-4" /> },
  ],
};
