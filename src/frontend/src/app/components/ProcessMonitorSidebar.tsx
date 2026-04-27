import { useState } from 'react';
import { FileJson, Terminal, Activity, Folder, TrendingUp, PieChart, RefreshCw, Zap, Cpu, Bot, Sparkles, Shield, BookOpen, Server } from 'lucide-react';
import { LogViewer } from './LogViewer';
import { ArtifactsSidebar, type ArtifactItem } from './ArtifactsSidebar';
import type { UsageStats } from './UsageHUD';

/**
 * 映射后端类型为可读的中文名称
 */
function getReadableBackendName(mode: string): { label: string; icon: React.ReactNode; description: string } {
  const modeMap: Record<string, { label: string; icon: React.ReactNode; description: string }> = {
    // 角色相关
    pm: { label: 'PM', icon: <Bot className="size-3 text-blue-400" />, description: 'PM 任务规划' },
    director: { label: 'Chief Engineer', icon: <Server className="size-3 text-purple-400" />, description: '代码执行' },
    architect: { label: 'Architect', icon: <BookOpen className="size-3 text-emerald-400" />, description: '架构设计' },
    chief_engineer: { label: 'Director', icon: <Shield className="size-3 text-amber-400" />, description: '技术审查' },
    qa: { label: 'QA', icon: <Shield className="size-3 text-red-400" />, description: '质量审查' },
    
    // Provider 类型
    generic: { label: '通用 Provider', icon: <Sparkles className="size-3 text-cyan-400" />, description: '默认运行时 Provider' },
    runtime_provider: { label: '运行时 Provider', icon: <Sparkles className="size-3 text-cyan-400" />, description: '配置的运行时 Provider' },
    codex: { label: 'Codex CLI', icon: <Bot className="size-3 text-green-400" />, description: 'Anthropic Codex' },
    ollama: { label: 'Ollama', icon: <Cpu className="size-3 text-orange-400" />, description: '本地 Ollama' },
    
    // 未知
    unknown: { label: '未知来源', icon: <Activity className="size-3 text-gray-400" />, description: '未识别的调用' },
  };
  
  const normalized = mode.toLowerCase().replace(' ', '_');
  return modeMap[normalized] || { 
    label: mode, 
    icon: <Activity className="size-3 text-gray-400" />, 
    description: `mode: ${mode}` 
  };
}

interface ProcessMonitorSidebarProps {
  onFileSelect: (file: ArtifactItem) => void;
  selectedFileId: string | null;
  onOpenWorkspace?: () => void;
  onOpenHistory?: () => void;
  fileStatusLines?: string[] | null;
  usageStats?: UsageStats | null;
  usageLoading?: boolean;
  usageError?: string | null;
  onRefreshUsage?: () => void;
}

type TabId = 'pm' | 'director' | 'files' | 'usage';

export function ProcessMonitorSidebar({
  onFileSelect,
  selectedFileId,
  onOpenWorkspace,
  onOpenHistory,
  fileStatusLines,
  usageStats,
  usageLoading = false,
  usageError,
  onRefreshUsage,
}: ProcessMonitorSidebarProps) {
  const [activeTab, setActiveTab] = useState<TabId>('pm');

  return (
    <div className="flex flex-col h-full bg-[linear-gradient(165deg,rgba(50,35,18,0.40),rgba(28,18,48,0.65),rgba(14,20,40,0.80))] text-text-main">
      {/* Sidebar Header & Tabs */}
      <div className="flex flex-col border-b border-white/5 bg-[linear-gradient(165deg,rgba(50,35,18,0.40),rgba(28,18,48,0.65))]">
        <div className="px-3 py-2 flex items-center gap-2">
            <Activity className="size-4 text-cyan-400" />
            <span className="text-xs font-bold tracking-wide text-amber-200">System Monitor</span>
            {(usageLoading || activeTab === 'usage') && usageStats === null && !usageError && (
              <RefreshCw className="size-3 text-cyan-400/50 animate-spin ml-auto" />
            )}
        </div>
        <div className="flex items-center px-1 pb-1 gap-1">
            <button
                onClick={() => setActiveTab('pm')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] uppercase font-bold tracking-wider rounded-t-mk transition-colors ${
                    activeTab === 'pm' 
                    ? 'bg-[var(--ink-indigo)] text-blue-400 border-t-2 border-blue-400' 
                    : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                }`}
            >
                <Terminal className="size-3" />
                PM Logs
            </button>
            <button
                onClick={() => setActiveTab('director')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] uppercase font-bold tracking-wider rounded-t-mk transition-colors ${
                    activeTab === 'director' 
                    ? 'bg-[var(--ink-indigo)] text-purple-400 border-t-2 border-purple-400' 
                    : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                }`}
            >
                <Terminal className="size-3" />
                Chief Engineer
            </button>
             <button
                onClick={() => setActiveTab('files')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] uppercase font-bold tracking-wider rounded-t-mk transition-colors ${
                    activeTab === 'files' 
                    ? 'bg-[var(--ink-indigo)] text-emerald-400 border-t-2 border-emerald-400' 
                    : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                }`}
            >
                <Folder className="size-3" />
                Artifacts
            </button>
             <button
                onClick={() => setActiveTab('usage')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] uppercase font-bold tracking-wider rounded-t-mk transition-colors ${
                    activeTab === 'usage' 
                    ? 'bg-[var(--ink-indigo)] text-yellow-400 border-t-2 border-yellow-400' 
                    : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                }`}
            >
                <PieChart className="size-3" />
                Usage
            </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-hidden relative">
         <div className={`absolute inset-0 ${activeTab === 'pm' ? 'z-10' : 'z-0 invisible'}`}>
            <LogViewer sourceId="pm-subprocess" className="h-full" />
         </div>
         <div className={`absolute inset-0 ${activeTab === 'director' ? 'z-10' : 'z-0 invisible'}`}>
            <LogViewer sourceId="director" className="h-full" />
         </div>
         <div className={`absolute inset-0 overflow-hidden flex flex-col ${activeTab === 'files' ? 'z-10' : 'z-0 invisible'}`}>
             <ArtifactsSidebar 
                 onFileSelect={onFileSelect}
                 selectedFileId={selectedFileId}
                 onOpenWorkspace={onOpenWorkspace}
                 onOpenHistory={onOpenHistory}
                 fileStatusLines={fileStatusLines}
             />
         </div>
         <div className={`absolute inset-0 overflow-y-auto ${activeTab === 'usage' ? 'z-10' : 'z-0 invisible'}`}>
            <div className="p-4 space-y-6">
                {/* Header with refresh button */}
                <div className="flex items-center justify-between">
                   <h3 className="text-xs uppercase font-bold text-amber-200/50 tracking-wider">Usage Stats</h3>
                   {onRefreshUsage && (
                     <button 
                       onClick={onRefreshUsage}
                       className="p-1.5 rounded hover:bg-white/5 text-amber-200/50 hover:text-cyan-400 transition-colors"
                       title="刷新Usage数据"
                     >
                       <RefreshCw className="size-3" />
                     </button>
                   )}
                </div>

                {/* Loading State */}
                {usageLoading && (
                  <div className="flex items-center justify-center py-8">
                    <RefreshCw className="size-5 text-cyan-400 animate-spin" />
                    <span className="ml-2 text-sm text-amber-200/50">加载Usage数据...</span>
                  </div>
                )}

                {/* Error State */}
                {usageError && (
                  <div className="p-3 rounded-lg border border-red-500/30 bg-red-500/10">
                    <div className="text-xs text-red-400">{usageError}</div>
                  </div>
                )}

                {/* Stats Display */}
                {usageStats && !usageLoading && (
                <div className="space-y-4">
                   <div className="grid grid-cols-2 gap-3">
                           <div className="bg-white/5 p-4 rounded-xl border border-cyan-500/20 shadow-[0_0_10px_rgba(34,211,238,0.1)]">
                               <div className="flex items-center gap-2 mb-2">
                                   <Cpu className="size-4 text-cyan-400" />
                                   <div className="text-[10px] text-cyan-500/70 uppercase tracking-wider">Token Usage</div>
                               </div>
                               <div className="text-3xl font-mono font-bold text-cyan-100">{usageStats.totals.total_tokens.toLocaleString()}</div>
                               <div className="text-[9px] text-cyan-500/50 mt-1">
                                 P: {(usageStats.totals.prompt_tokens/1000).toFixed(1)}k / C: {(usageStats.totals.completion_tokens/1000).toFixed(1)}k
                               </div>
                           </div>
                           <div className="bg-white/5 p-4 rounded-xl border border-purple-500/20 shadow-[0_0_10px_rgba(168,85,247,0.1)]">
                               <div className="flex items-center gap-2 mb-2">
                                   <Zap className="size-4 text-purple-400" />
                                   <div className="text-[10px] text-purple-500/70 uppercase tracking-wider">LLM Calls</div>
                               </div>
                               <div className="text-3xl font-mono font-bold text-purple-100">{usageStats.calls.toLocaleString()}</div>
                               <div className="text-[9px] text-purple-500/50 mt-1">
                                 {usageStats.estimated_calls > 0 && `估算: ${usageStats.estimated_calls}`}
                               </div>
                           </div>
                   </div>

                {/* Cost Estimation - Optional */}
                {usageStats.totals.total_tokens > 0 && (
                    <div className="p-3 rounded-lg border border-amber-500/20 bg-amber-500/5">
                        <div className="text-[10px] uppercase tracking-wider text-amber-200/50 mb-1">Cost Estimate</div>
                        <div className="flex items-baseline gap-2">
                            <span className="text-lg font-mono text-amber-200">${(usageStats.totals.total_tokens / 1000 * 0.003).toFixed(3)}</span>
                            <span className="text-[10px] text-amber-200/40">≈ $3/1M tokens (Claude)</span>
                        </div>
                    </div>
                )}

                {usageStats?.by_mode && Object.keys(usageStats.by_mode).length > 0 && (
                     <div className="space-y-2">
                        <h3 className="text-xs uppercase font-bold text-amber-200/50 tracking-wider">Breakdown</h3>
                        <div className="space-y-1.5">
                            {Object.entries(usageStats.by_mode)
                                .sort(([, a], [, b]) => b.total_tokens - a.total_tokens)
                                .map(([mode, stats]) => {
                                    const backend = getReadableBackendName(mode);
                                    return (
                                    <div key={mode} className="flex items-center justify-between text-xs bg-white/5 px-3 py-2.5 rounded-lg border border-white/5 hover:border-cyan-500/20 transition-colors">
                                        <div className="flex items-center gap-2">
                                            {backend.icon}
                                            <div className="flex flex-col">
                                                <span className="font-medium text-amber-100/80">{backend.label}</span>
                                                <span className="text-[9px] text-amber-200/30">{backend.description}</span>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <span className="text-amber-200/40 font-mono text-[10px]">{stats.calls} 次</span>
                                            <span className="text-cyan-300 font-mono font-medium">{stats.total_tokens.toLocaleString()} tks</span>
                                        </div>
                                    </div>
                                    );
                                })}
                        </div>
                     </div>
                )}
                </div>
                )}

                {/* Empty State - No data yet */}
                {!usageStats && !usageLoading && !usageError && (
                   <div className="flex flex-col items-center justify-center py-12 text-center">
                       <PieChart className="size-10 text-amber-200/20 mb-3" />
                       <div className="text-sm text-amber-200/40 italic">暂无Usage记录</div>
                       <div className="text-[10px] text-amber-200/25 mt-1">运行 PM/Director 后将显示统计数据</div>
                   </div>
                )}
            </div>
         </div>
      </div>
    </div>
  );
}
