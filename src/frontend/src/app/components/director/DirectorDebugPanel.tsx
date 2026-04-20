/**
 * DirectorDebugPanel - 调试面板展示组件
 */
import { Bug, CheckCircle2 } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import type { ExecutionTask } from './hooks/useDirectorWorkspace';

interface DirectorDebugPanelProps {
  tasks: ExecutionTask[];
}

export function DirectorDebugPanel({ tasks }: DirectorDebugPanelProps) {
  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center px-4 border-b border-white/5">
        <h2 className="text-sm font-medium text-slate-200">调试中心</h2>
      </div>
      <div className="flex-1 overflow-auto p-4">
        {tasks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <CheckCircle2 className="w-12 h-12 mb-4 text-blue-500/30" />
            <p>没有需要调试的问题</p>
          </div>
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => (
              <div
                key={task.id}
                className="p-4 rounded-xl border border-red-500/20 bg-red-500/5"
              >
                <div className="flex items-center gap-2 mb-2">
                  <Bug className="w-4 h-4 text-red-400" />
                  <span className="text-sm text-slate-200 font-medium">{task.name}</span>
                </div>
                {task.error && (
                  <pre className="text-xs text-red-400 font-mono bg-red-950/30 p-2 rounded">
                    {task.error}
                  </pre>
                )}
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="outline" className="border-red-500/30 text-red-400">
                    调试
                  </Button>
                  <Button size="sm" variant="ghost" className="text-slate-400">
                    跳过
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
