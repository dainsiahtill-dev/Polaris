/**
 * DirectorCodePanel - 代码面板展示组件
 */
import { useState } from 'react';
import { FilePlus, FileX, FileEdit, FileCode } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { RealTimeFileDiff } from './RealTimeFileDiff';
import type { FileEditEvent } from '@/app/hooks/useRuntime';

interface DirectorCodePanelProps {
  workspace: string;
  fileEditEvents: FileEditEvent[];
}

export function DirectorCodePanel({ workspace, fileEditEvents }: DirectorCodePanelProps) {
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  const getOperationIcon = (operation: string) => {
    switch (operation) {
      case 'create':
        return <FilePlus className="w-3.5 h-3.5 text-emerald-400" />;
      case 'delete':
        return <FileX className="w-3.5 h-3.5 text-red-400" />;
      case 'modify':
      default:
        return <FileEdit className="w-3.5 h-3.5 text-blue-400" />;
    }
  };

  const getOperationLabel = (operation: string) => {
    switch (operation) {
      case 'create':
        return '创建';
      case 'delete':
        return '删除';
      case 'modify':
        return '修改';
      default:
        return operation;
    }
  };

  const getOperationColor = (operation: string) => {
    switch (operation) {
      case 'create':
        return 'text-emerald-400';
      case 'delete':
        return 'text-red-400';
      case 'modify':
        return 'text-blue-400';
      default:
        return 'text-slate-400';
    }
  };

  // 只显示最近的 20 个事件，按时间倒序
  const recentEvents = [...fileEditEvents].reverse().slice(0, 20);

  const toggleExpand = (eventId: string) => {
    setExpandedEventId((prev) => (prev === eventId ? null : eventId));
  };

  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-slate-200">实时代码变更</h2>
          {fileEditEvents.length > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400">
              {fileEditEvents.length} 个文件
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="text-slate-400">
            <FileCode className="w-4 h-4 mr-1.5" />
            打开文件
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden flex">
        {/* 文件变更列表 + Diff 详情 */}
        <div className="flex-1 overflow-auto p-4">
          {recentEvents.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-500">
              <FileCode className="w-12 h-12 mb-4 text-indigo-500/30" />
              <p>等待代码变更...</p>
              <p className="text-xs mt-2 opacity-70">Director 执行时将实时显示文件修改</p>
            </div>
          ) : (
            <div className="space-y-2">
              {recentEvents.map((event, index) => (
                <div key={event.id}>
                  <div
                    className={cn(
                      'p-3 rounded-xl border transition-all cursor-pointer',
                      index === 0 ? 'bg-indigo-500/10 border-indigo-500/30' : 'bg-white/5 border-white/5 hover:border-white/10',
                      expandedEventId === event.id && 'ring-1 ring-indigo-500/30'
                    )}
                    onClick={() => toggleExpand(event.id)}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5">{getOperationIcon(event.operation)}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs font-mono text-slate-300 truncate flex-1" title={event.filePath}>
                            {event.filePath}
                          </span>
                          <span
                            className={cn(
                              'text-[10px] px-1.5 py-0.5 rounded bg-white/10',
                              getOperationColor(event.operation)
                            )}
                          >
                            {getOperationLabel(event.operation)}
                          </span>
                          {event.patch && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400">
                              Diff
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                          <span>{event.contentSize} bytes</span>
                          {event.taskId && <span className="text-slate-600">任务: {event.taskId.slice(0, 8)}</span>}
                          <span className="text-slate-600">
                            {new Date(event.timestamp).toLocaleTimeString()}
                          </span>
                          {event.patch && (
                            <span className="text-cyan-400">
                              {expandedEventId === event.id ? '▼ 收起' : '▶ 展开 Diff'}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 展开的 Diff 详情 */}
                  {expandedEventId === event.id && event.patch && (
                    <div className="mt-2">
                      <RealTimeFileDiff
                        filePath={event.filePath}
                        operation={event.operation}
                        patch={event.patch}
                        compact
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧统计 */}
        <div className="w-48 border-l border-white/5 p-4 bg-slate-950/30">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">变更统计</h3>
          <div className="space-y-2">
            <div className="flex items-center justify-between p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <span className="text-xs text-emerald-400 flex items-center gap-1.5">
                <FilePlus className="w-3 h-3" />
                创建
              </span>
              <span className="text-xs font-mono text-emerald-300">
                {fileEditEvents.filter((e) => e.operation === 'create').length}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <span className="text-xs text-blue-400 flex items-center gap-1.5">
                <FileEdit className="w-3 h-3" />
                修改
              </span>
              <span className="text-xs font-mono text-blue-300">
                {fileEditEvents.filter((e) => e.operation === 'modify').length}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-lg bg-red-500/10 border border-red-500/20">
              <span className="text-xs text-red-400 flex items-center gap-1.5">
                <FileX className="w-3 h-3" />
                删除
              </span>
              <span className="text-xs font-mono text-red-300">
                {fileEditEvents.filter((e) => e.operation === 'delete').length}
              </span>
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-white/5">
            <h3 className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">工作区</h3>
            <p className="text-xs text-slate-400 truncate" title={workspace}>
              {workspace}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
