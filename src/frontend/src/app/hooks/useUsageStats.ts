import { useState, useEffect, useCallback } from 'react';
import type { UsageStats } from '@/app/components/UsageHUD';
import { readFile } from '@/services/fileService';

interface RuntimeUsageData {
  totals: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  calls: number;
  by_mode?: Record<string, { total_tokens: number; calls: number }>;
}

export const LLM_OBSERVATIONS_LOGICAL_PATH = 'runtime/events/llm.observations.jsonl';

/**
 * Hook to fetch and manage usage statistics
 * Data is read from runtime events JSONL files
 */
export function useUsageStats(workspace: string | null) {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    if (!workspace) {
      setStats(null);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Always read via the logical runtime path so storage-layout changes do not break the HUD.
      const result = await readFile(LLM_OBSERVATIONS_LOGICAL_PATH, 500);

      if (!result.ok || !result.data) {
        // If no usage file exists, return null silently
        setStats(null);
        return;
      }

      const content = result.data.content as string | undefined;
      
      if (!content) {
        setStats(null);
        return;
      }

      // Parse JSONL content
      const lines = content.split('\n').filter(line => line.trim());
      const usageData: RuntimeUsageData = {
        totals: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        calls: 0,
        by_mode: {}
      };

      for (const line of lines) {
        try {
          const entry = JSON.parse(line);
          if (entry.kind === 'observation' && entry.output?.usage) {
            const usage = entry.output.usage;
            const mode = entry.refs?.mode || 'unknown';
            
            // Aggregate totals
            usageData.totals.prompt_tokens += usage.prompt_tokens || 0;
            usageData.totals.completion_tokens += usage.completion_tokens || 0;
            usageData.totals.total_tokens += usage.total_tokens || 0;
            usageData.calls += 1;

            // Aggregate by mode
            if (!usageData.by_mode) {
              usageData.by_mode = {};
            }
            if (!usageData.by_mode[mode]) {
              usageData.by_mode[mode] = { total_tokens: 0, calls: 0 };
            }
            usageData.by_mode[mode].total_tokens += usage.total_tokens || 0;
            usageData.by_mode[mode].calls += 1;
          }
        } catch {
          // Skip invalid JSON lines
        }
      }

      setStats({
        totals: usageData.totals,
        calls: usageData.calls,
        estimated_calls: 0,
        by_mode: usageData.by_mode
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch usage stats');
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    fetchStats();
    
    // Poll every 30 seconds
    const interval = setInterval(fetchStats, 30000);
    return () => clearInterval(interval);
  }, [fetchStats]);

  return {
    stats,
    loading,
    error,
    refresh: fetchStats
  };
}
