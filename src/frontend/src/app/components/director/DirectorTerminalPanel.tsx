/**
 * DirectorTerminalPanel - 终端面板展示组件
 */
import { RotateCcw } from 'lucide-react';
import { Button } from '@/app/components/ui/button';

interface DirectorTerminalPanelProps {
  output: string;
}

export function DirectorTerminalPanel({ output }: DirectorTerminalPanelProps) {
  return (
    <div className="h-full flex flex-col">
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <h2 className="text-sm font-medium text-slate-200">执行终端</h2>
        <Button variant="ghost" size="sm" className="text-slate-400">
          <RotateCcw className="w-4 h-4 mr-1.5" />
          清空
        </Button>
      </div>
      <div className="flex-1 p-4">
        <div className="h-full rounded-xl border border-white/10 bg-slate-950 p-4 font-mono text-xs overflow-auto">
          {output ? (
            <pre className="text-slate-300 whitespace-pre-wrap">{output}</pre>
          ) : (
            <div className="text-slate-600">等待执行...</div>
          )}
        </div>
      </div>
    </div>
  );
}
