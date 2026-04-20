/**
 * Unified Connectivity Store
 * 统一管理所有连通性测试状态，解决组件间状态同步问题
 *
 * 已迁移到 Zustand: 使用 providerStore 而非 ProviderContext
 */

import { useCallback } from 'react';
import type { ProviderConfig } from '../types';
import type { ConnectivityStatus } from './providerReducer';
import type { ConnectivityResultStrict } from '../types/strict';
import type { ConnectivityResult, InterviewProviderSummary, InterviewRoleSummary } from '../interview/InterviewHall';
import { useProviderStore } from '@/app/store';
import { resolveProviderConfiguredModel } from '../utils/providerModelResolver';

export type {
  InterviewProviderSummary,
  ConnectivityResult,
  InterviewRoleSummary
};

export interface ProviderConnectivitySummary {
  id: string;
  name: string;
  model: string;
  providerType: string;
  status: 'ready' | 'testing' | 'failed' | 'untested';
  lastTest?: {
    timestamp: string;
    success: boolean;
    latencyMs?: number;
    error?: string;
  };
}

export type RoleId = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';

export function isConnectivityKeyForProvider(key: string, providerId: string): boolean {
  if (!key || !providerId) return false;
  return key === providerId || key.endsWith(`::${providerId}`);
}

interface UseConnectivityStoreReturn {
  getProviderStatus: (providerId: string) => ConnectivityStatus;
  getConnectivityResult: (key: string) => ConnectivityResult | undefined;
  getProviderConnectivity: (providerId: string) => ConnectivityResult | undefined;
  buildProviderSummaries: (providers: Record<string, ProviderConfig>) => ProviderConnectivitySummary[];
  buildConnectivityMap: () => Map<string, ConnectivityResult>;
  getRoleProviderConnectivity: (roleId: string, providerId: string, model?: string) => ConnectivityResult | undefined;
  getLatestProviderConnectivity: (providerId: string) => ConnectivityResult | undefined;
  isProviderReady: (providerId: string) => boolean;
}

function convertToConnectivityResult(result: ConnectivityResultStrict): ConnectivityResult {
  return {
    ok: result.ok,
    timestamp: result.timestamp,
    latencyMs: result.latencyMs,
    error: result.error,
    model: result.model,
    sourceRole: result.sourceRole,
    thinking: result.thinking,
  };
}

function mapConnectivityStatusToInterview(status: ConnectivityStatus): 'ready' | 'testing' | 'failed' | 'untested' {
  switch (status) {
    case 'success':
      return 'ready';
    case 'running':
      return 'testing';
    case 'failed':
      return 'failed';
    default:
      return 'untested';
  }
}

export function useConnectivityStore(): UseConnectivityStoreReturn {
  const providerTestStatus = useProviderStore((s) => s.providerTestStatus);
  const connectivityResults = useProviderStore((s) => s.connectivityResults);

  const getLatestProviderConnectivity = useCallback((providerId: string): ConnectivityResult | undefined => {
    let latestResult: ConnectivityResult | undefined = undefined;
    let latestTimestamp = 0;

    Object.entries(connectivityResults).forEach(([key, result]) => {
      if (isConnectivityKeyForProvider(key, providerId)) {
        const timestamp = new Date(result.timestamp).getTime();
        if (timestamp > latestTimestamp) {
          latestTimestamp = timestamp;
          latestResult = convertToConnectivityResult(result);
        }
      }
    });

    return latestResult;
  }, [connectivityResults]);

  const getProviderStatus = useCallback((providerId: string): ConnectivityStatus => {
    return providerTestStatus[providerId] || 'unknown';
  }, [providerTestStatus]);

  const getConnectivityResult = useCallback((key: string): ConnectivityResult | undefined => {
    const result = connectivityResults[key];
    if (!result) return undefined;
    return convertToConnectivityResult(result);
  }, [connectivityResults]);

  const getProviderConnectivity = useCallback((providerId: string): ConnectivityResult | undefined => {
    return getLatestProviderConnectivity(providerId);
  }, [getLatestProviderConnectivity]);

  const buildProviderSummaries = useCallback((providers: Record<string, ProviderConfig>): ProviderConnectivitySummary[] => {
    return Object.entries(providers).map(([providerId, provider]) => {
      const status = getProviderStatus(providerId);
      const latestConnectivity = getProviderConnectivity(providerId);

      return {
        id: providerId,
        name: provider.name || providerId,
        model: resolveProviderConfiguredModel(provider),
        providerType: provider.type || 'unknown',
        status: mapConnectivityStatusToInterview(status),
        lastTest: latestConnectivity ? {
          timestamp: latestConnectivity.timestamp,
          success: latestConnectivity.ok,
          latencyMs: latestConnectivity.latencyMs,
          error: latestConnectivity.error,
        } : undefined,
      };
    });
  }, [getProviderStatus, getProviderConnectivity]);

  const buildConnectivityMap = useCallback((): Map<string, ConnectivityResult> => {
    const result = new Map<string, ConnectivityResult>();
    Object.entries(connectivityResults).forEach(([key, value]) => {
      result.set(key, convertToConnectivityResult(value));
    });
    return result;
  }, [connectivityResults]);

  const getRoleProviderConnectivity = useCallback((
    roleId: string,
    providerId: string,
    model?: string
  ): ConnectivityResult | undefined => {
    const key = `${roleId}::${providerId}`;
    const directResult = getConnectivityResult(key);

    if (directResult && (!model || directResult.model === model)) {
      return directResult;
    }

    return getLatestProviderConnectivity(providerId);
  }, [getConnectivityResult, getLatestProviderConnectivity]);

  const isProviderReady = useCallback((providerId: string): boolean => {
    const status = getProviderStatus(providerId);
    return status === 'success';
  }, [getProviderStatus]);

  return {
    getProviderStatus,
    getConnectivityResult,
    getProviderConnectivity,
    buildProviderSummaries,
    buildConnectivityMap,
    getRoleProviderConnectivity,
    getLatestProviderConnectivity,
    isProviderReady,
  };
}

export function useRoleProviderConnectivity(
  roleId: string,
  providerId: string,
  model?: string
): {
  status: ConnectivityStatus;
  result: ConnectivityResult | undefined;
  latency?: number;
  error?: string;
  timestamp?: string;
} {
  const { getProviderStatus, getConnectivityResult, getLatestProviderConnectivity } = useConnectivityStore();

  const status = getProviderStatus(providerId);
  const directResult = getConnectivityResult(`${roleId}::${providerId}`);

  let result: ConnectivityResult | undefined;
  if (directResult && (!model || directResult.model === model)) {
    result = directResult;
  } else {
    result = getLatestProviderConnectivity(providerId);
  }

  return {
    status,
    result,
    latency: result?.latencyMs,
    error: result?.error,
    timestamp: result?.timestamp,
  };
}

export function useProviderReadiness(providerId: string): {
  isReady: boolean;
  status: ConnectivityStatus;
  lastTest?: {
    timestamp: string;
    success: boolean;
    latencyMs?: number;
    error?: string;
  };
} {
  const { getProviderStatus, getProviderConnectivity } = useConnectivityStore();

  const status = getProviderStatus(providerId);
  const connectivity = getProviderConnectivity(providerId);

  return {
    isReady: status === 'success',
    status,
    lastTest: connectivity ? {
      timestamp: connectivity.timestamp,
      success: connectivity.ok,
      latencyMs: connectivity.latencyMs,
      error: connectivity.error,
    } : undefined,
  };
}
