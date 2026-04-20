import { Activity, Cpu, Zap } from 'lucide-react';

export interface UsageStats {
  totals: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  calls: number;
  estimated_calls: number;
  by_mode?: Record<string, { total_tokens: number; calls: number }>;
}

export function UsageHUD({ stats }: { stats?: UsageStats | null }) {
  if (!stats) return null;

  return (
    <div className="no-drag flex items-center gap-3 px-3 py-1 bg-black/40 rounded-lg border border-cyan-500/20 backdrop-blur-md shadow-[0_0_10px_rgba(6,182,212,0.1)]">
       <div className="flex items-center gap-1.5" title={`Prompt: ${stats.totals.prompt_tokens.toLocaleString()}, Completion: ${stats.totals.completion_tokens.toLocaleString()}`}>
         <Cpu className="size-3.5 text-cyan-400" />
         <span className="text-[10px] font-mono font-bold text-cyan-100">{stats.totals.total_tokens.toLocaleString()}</span>
         <span className="text-[9px] text-cyan-500/70 font-bold tracking-wider">TKS</span>
       </div>
       
       <div className="w-px h-3 bg-white/10" />
       
       <div className="flex items-center gap-1.5" title="LLM Calls">
         <Zap className="size-3.5 text-purple-400" />
         <span className="text-[10px] font-mono font-bold text-purple-100">{stats.calls}</span>
         {stats.estimated_calls > 0 && (
            <span className="text-[9px] px-0.5 rounded bg-yellow-500/20 text-yellow-400 font-bold" title={`${stats.estimated_calls} estimated calls`}>
                EST
            </span>
         )}
         <span className="text-[9px] text-purple-500/70 font-bold tracking-wider">OPS</span>
       </div>
    </div>
  );
}
