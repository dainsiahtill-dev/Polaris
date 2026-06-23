import { Activity, Cpu, Zap } from 'lucide-react';

export interface UsageStats {
  totals: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cached_tokens?: number;
    cache_creation_tokens?: number;
    cache_read_tokens?: number;
    tool_tokens?: number;
    reasoning_tokens?: number;
    audio_tokens?: number;
  };
  calls: number;
  estimated_calls: number;
  by_mode?: Record<string, { total_tokens: number; calls: number }>;
}

export function UsageHUD({ stats }: { stats?: UsageStats | null }) {
  if (!stats) return null;

  return (
    <div className="no-drag flex items-center gap-3 px-3 py-1 soft-panel-subtle rounded-lg">
       <div className="flex items-center gap-1.5" title={`Prompt: ${stats.totals.prompt_tokens.toLocaleString()}, Completion: ${stats.totals.completion_tokens.toLocaleString()}`}>
         <Cpu className="size-3.5 text-accent" />
         <span className="text-[10px] font-mono font-bold text-text-main">{stats.totals.total_tokens.toLocaleString()}</span>
         <span className="text-[9px] text-text-dim font-bold tracking-wider">TKS</span>
       </div>
       
       <div className="w-px h-3 bg-white/10" />
       
       <div className="flex items-center gap-1.5" title="LLM Calls">
         <Zap className="size-3.5 text-accent" />
         <span className="text-[10px] font-mono font-bold text-text-main">{stats.calls}</span>
         {stats.estimated_calls > 0 && (
            <span className="text-[9px] px-0.5 rounded bg-yellow-500/20 text-yellow-400 font-bold" title={`${stats.estimated_calls} estimated calls`}>
                EST
            </span>
         )}
         <span className="text-[9px] text-text-dim font-bold tracking-wider">OPS</span>
       </div>
    </div>
  );
}
