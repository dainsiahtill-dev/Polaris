/**
 * HealthStatus - Health status indicator
 *
 * Features:
 * - Green/yellow/red indicator based on /v2/health
 * - Auto-refresh every 30s
 * - Show detailed status on click
 */

import { useState, useCallback, useEffect } from 'react';
import { useHealth } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';

const REFRESH_INTERVAL_MS = 30000;

export interface HealthStatusProps {
  autoRefresh?: boolean;
  refreshIntervalMs?: number;
}

type HealthColor = 'green' | 'yellow' | 'red' | 'gray';

function getHealthColor(status?: string): HealthColor {
  const token = (status || '').toLowerCase();
  if (token === 'healthy' || token === 'ok') return 'green';
  if (token === 'degraded') return 'yellow';
  if (token === 'unhealthy' || token === 'error') return 'red';
  return 'gray';
}

function healthColorClasses(color: HealthColor): string {
  switch (color) {
    case 'green':
      return 'bg-green-500';
    case 'yellow':
      return 'bg-yellow-500';
    case 'red':
      return 'bg-red-500';
    case 'gray':
    default:
      return 'bg-gray-400';
  }
}

function healthLabel(color: HealthColor): string {
  switch (color) {
    case 'green':
      return 'Healthy';
    case 'yellow':
      return 'Degraded';
    case 'red':
      return 'Unhealthy';
    case 'gray':
    default:
      return 'Unknown';
  }
}

export function HealthStatus({
  autoRefresh = true,
  refreshIntervalMs = REFRESH_INTERVAL_MS,
}: HealthStatusProps): JSX.Element {
  const { health, loading, error, check } = useHealth();
  const { apiError } = useV2ApiError();
  const [showDetails, setShowDetails] = useState(false);

  const color = getHealthColor(health?.status);
  const label = health?.status ? health.status : healthLabel(color);

  const handleToggleDetails = useCallback(() => {
    setShowDetails((prev) => !prev);
  }, []);

  const handleRefresh = useCallback(() => {
    void check();
  }, [check]);

  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      void check();
    }, refreshIntervalMs);

    return () => {
      clearInterval(interval);
    };
  }, [autoRefresh, refreshIntervalMs, check]);

  useEffect(() => {
    if (error) {
      apiError.setError({ code: 'HEALTH_CHECK_ERROR', message: error, status: 500 });
    }
  }, [error, apiError]);

  return (
    <div className="inline-flex flex-col gap-2">
      <button
        onClick={handleToggleDetails}
        className="flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
        aria-label="Toggle health details"
      >
        <span
          className={`h-3 w-3 rounded-full ${healthColorClasses(color)} ${loading ? 'animate-pulse' : ''}`}
          aria-hidden="true"
        />
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
          {label}
        </span>
        {loading && (
          <span className="text-xs text-gray-400">checking...</span>
        )}
      </button>

      {showDetails && (
        <div className="border rounded-lg p-3 bg-white dark:bg-gray-900 shadow-sm min-w-[240px]">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Health Details
            </h3>
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50"
            >
              Refresh
            </button>
          </div>

          {health && (
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500 dark:text-gray-400">Status</dt>
                <dd className="font-medium text-gray-900 dark:text-gray-100">{health.status || 'N/A'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500 dark:text-gray-400">Version</dt>
                <dd className="font-medium text-gray-900 dark:text-gray-100">{health.version || 'N/A'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500 dark:text-gray-400">Timestamp</dt>
                <dd className="font-medium text-gray-900 dark:text-gray-100">
                  {health.timestamp ? new Date(health.timestamp).toLocaleString() : 'N/A'}
                </dd>
              </div>
            </dl>
          )}

          {(error || apiError.hasError) && (
            <div className="mt-2 text-xs text-red-600 dark:text-red-400">
              {error || apiError.error?.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
