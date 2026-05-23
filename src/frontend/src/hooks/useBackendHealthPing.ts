import { useCallback, useState } from 'react';
import { healthV2Service } from '@/services/api';
import type { HealthV2Response } from '@/services/api.types';

export type BackendHealthPingStatus = 'idle' | 'checking' | 'healthy' | 'unhealthy';

interface BackendHealthPingState {
  status: BackendHealthPingStatus;
  evidence: string;
  error: string | null;
  checkedAt: string | null;
}

function normalizeHealthStatus(payload: HealthV2Response | undefined): BackendHealthPingStatus {
  if (payload?.ok === true) {
    return 'healthy';
  }

  const status = String(payload?.status || '').trim().toLowerCase();
  if (['ok', 'ready', 'healthy', 'up'].includes(status)) {
    return 'healthy';
  }
  if (['error', 'failed', 'unhealthy', 'down', 'unavailable'].includes(status)) {
    return 'unhealthy';
  }
  return payload ? 'healthy' : 'unhealthy';
}

function formatHealthEvidence(payload: HealthV2Response | undefined, status: BackendHealthPingStatus): string {
  if (!payload) {
    return '/v2/health · no payload';
  }
  const parts = [
    '/v2/health',
    status,
  ];
  if (payload.version) {
    parts.push(`version=${payload.version}`);
  }
  if (payload.timestamp) {
    parts.push(`timestamp=${payload.timestamp}`);
  }
  if (typeof payload.lancedb_ok === 'boolean') {
    parts.push(`lancedb=${payload.lancedb_ok ? 'ok' : 'failed'}`);
  }
  return parts.join(' · ');
}

export function useBackendHealthPing() {
  const [state, setState] = useState<BackendHealthPingState>({
    status: 'idle',
    evidence: '/v2/health · not checked',
    error: null,
    checkedAt: null,
  });

  const ping = useCallback(async (): Promise<boolean> => {
    setState({
      status: 'checking',
      evidence: '/v2/health · checking',
      error: null,
      checkedAt: null,
    });

    try {
      const result = await healthV2Service.check();
      if (!result.ok || !result.data) {
        const message = result.error || 'Health check failed';
        setState({
          status: 'unhealthy',
          evidence: `/v2/health · ${message}`,
          error: message,
          checkedAt: new Date().toISOString(),
        });
        return false;
      }

      const status = normalizeHealthStatus(result.data);
      const evidence = formatHealthEvidence(result.data, status);
      setState({
        status,
        evidence,
        error: status === 'unhealthy' ? result.data.lancedb_error || null : null,
        checkedAt: result.data.timestamp || new Date().toISOString(),
      });
      return status === 'healthy';
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Health check failed';
      setState({
        status: 'unhealthy',
        evidence: `/v2/health · ${message}`,
        error: message,
        checkedAt: new Date().toISOString(),
      });
      return false;
    }
  }, []);

  return {
    ...state,
    ping,
  };
}
