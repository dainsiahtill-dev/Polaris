import { useEffect, useMemo, useRef, useState } from 'react';
import { fileV2Service } from '@/services/api';
import {
  buildTelemetryFromStream,
  EMPTY_TELEMETRY,
  mergeTelemetry,
  parseJournalLines,
  type ContextOSTelemetry,
} from './contextOSTelemetry';

export interface UseContextOSTelemetryOptions {
  workspace: string;
  runId?: string | null;
  intervalMs?: number;
  tailLines?: number;
  wsTelemetry?: ContextOSTelemetry | null;
  enabled?: boolean;
}

export interface UseContextOSTelemetryResult {
  telemetry: ContextOSTelemetry;
  loading: boolean;
  error: string | null;
  lastPollAt: number | null;
}

function resolveJournalPath(runId?: string | null): string {
  if (runId) {
    return `runtime/runs/${runId}/logs/journal.enriched.jsonl`;
  }
  return 'runtime/logs/journal.enriched.jsonl';
}

export function useContextOSTelemetry(options: UseContextOSTelemetryOptions): UseContextOSTelemetryResult {
  const { workspace, runId, intervalMs = 4000, tailLines = 400, wsTelemetry, enabled = true } = options;
  const [fileTelemetry, setFileTelemetry] = useState<ContextOSTelemetry>(EMPTY_TELEMETRY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastPollAt, setLastPollAt] = useState<number | null>(null);
  const abortRef = useRef(false);

  const journalPath = useMemo(() => resolveJournalPath(runId), [runId]);

  useEffect(() => {
    if (!enabled) return;
    abortRef.current = false;

    const poll = async () => {
      setLoading(true);
      try {
        const result = await fileV2Service.readTail(journalPath, tailLines);
        if (abortRef.current) return;
        if (!result.ok || !result.data) {
          setError(result.error || 'Failed to read journal');
          return;
        }
        const content = String(result.data.content || '');
        const logs = parseJournalLines(content);
        const telemetry = buildTelemetryFromStream(logs, [], []);
        setFileTelemetry(telemetry);
        setError(null);
        setLastPollAt(Date.now());
      } catch (err) {
        if (abortRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!abortRef.current) setLoading(false);
      }
    };

    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      abortRef.current = true;
      clearInterval(id);
    };
  }, [enabled, journalPath, tailLines, intervalMs]);

  const telemetry = useMemo(
    () => mergeTelemetry(wsTelemetry ?? EMPTY_TELEMETRY, fileTelemetry),
    [wsTelemetry, fileTelemetry],
  );

  return { telemetry, loading, error, lastPollAt };
}
