/**
 * FactoryRunMonitor - Factory run monitoring
 *
 * Features:
 * - List of recent runs
 * - Status badges (pending, running, completed, failed)
 * - Cancel button
 * - View artifacts link
 */

import { useState, useCallback } from 'react';
import { useFactoryRuns } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';

export interface FactoryRunMonitorProps {
  runId: string;
  onCancel?: (runId: string) => void;
  onViewArtifacts?: (runId: string) => void;
}

type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'unknown';

function normalizeStatus(type?: string, stage?: string): RunStatus {
  const token = (type || stage || '').toLowerCase();
  if (token.includes('pend')) return 'pending';
  if (token.includes('run') || token.includes('start') || token.includes('progress')) return 'running';
  if (token.includes('complete') || token.includes('success') || token.includes('done')) return 'completed';
  if (token.includes('fail') || token.includes('error') || token.includes('abort')) return 'failed';
  return 'unknown';
}

function statusBadgeClasses(status: RunStatus): string {
  switch (status) {
    case 'pending':
      return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300';
    case 'running':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300';
    case 'completed':
      return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300';
    case 'failed':
      return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300';
    case 'unknown':
    default:
      return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300';
  }
}

export function FactoryRunMonitor({
  runId,
  onCancel,
  onViewArtifacts,
}: FactoryRunMonitorProps): JSX.Element {
  const { events, auditBundle, loading, error, fetchEvents, fetchAuditBundle } = useFactoryRuns();
  const { apiError } = useV2ApiError();
  const [expanded, setExpanded] = useState(false);

  const handleLoad = useCallback(() => {
    void fetchEvents(runId, { limit: 50 });
  }, [fetchEvents, runId]);

  const handleToggleExpand = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next && !events) {
        void fetchEvents(runId, { limit: 50 });
      }
      return next;
    });
  }, [events, fetchEvents, runId]);

  const handleViewAudit = useCallback(() => {
    void fetchAuditBundle(runId);
  }, [fetchAuditBundle, runId]);

  const handleCancel = useCallback(() => {
    onCancel?.(runId);
  }, [onCancel, runId]);

  const handleViewArtifacts = useCallback(() => {
    onViewArtifacts?.(runId);
  }, [onViewArtifacts, runId]);

  const latestEvent = events?.events?.[events.events.length - 1];
  const status: RunStatus = normalizeStatus(latestEvent?.type, latestEvent?.stage);

  return (
    <div className="border rounded-lg bg-white dark:bg-gray-900">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            Factory Run
          </h3>
          <span
            className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClasses(status)}`}
          >
            {status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleLoad}
            disabled={loading}
            className="text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            onClick={handleToggleExpand}
            className="text-xs text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-700 dark:text-gray-300">
            <span className="font-medium">Run ID:</span>{' '}
            <code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">{runId}</code>
          </div>
          <div className="flex gap-2">
            {status === 'running' && (
              <button
                onClick={handleCancel}
                className="px-3 py-1 text-xs font-medium text-red-700 bg-red-50 dark:bg-red-900/20 dark:text-red-300 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors"
              >
                Cancel
              </button>
            )}
            <button
              onClick={handleViewArtifacts}
              className="px-3 py-1 text-xs font-medium text-blue-700 bg-blue-50 dark:bg-blue-900/20 dark:text-blue-300 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors"
            >
              View Artifacts
            </button>
            <button
              onClick={handleViewAudit}
              className="px-3 py-1 text-xs font-medium text-gray-700 bg-gray-50 dark:bg-gray-800 dark:text-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              Audit
            </button>
          </div>
        </div>

        {events && events.total !== undefined && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            {events.total} event(s)
          </p>
        )}
      </div>

      {expanded && events && events.events && events.events.length > 0 && (
        <div className="border-t px-4 py-3 max-h-64 overflow-y-auto">
          <ul className="space-y-2">
            {events.events.map((event, index) => {
              const eventStatus = normalizeStatus(event.type, event.stage);
              return (
                <li
                  key={event.event_id || `${runId}-${index}`}
                  className="text-sm border rounded p-2 bg-gray-50 dark:bg-gray-800/50"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-gray-800 dark:text-gray-200">
                      {event.type || 'Event'}
                    </span>
                    <span
                      className={`inline-flex px-1.5 py-0.5 text-xs rounded ${statusBadgeClasses(eventStatus)}`}
                    >
                      {eventStatus}
                    </span>
                  </div>
                  {event.stage && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      Stage: {event.stage}
                    </p>
                  )}
                  {event.message && (
                    <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5">
                      {event.message}
                    </p>
                  )}
                  {event.timestamp && (
                    <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      {new Date(event.timestamp).toLocaleString()}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {expanded && events && events.events && events.events.length === 0 && (
        <div className="border-t px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-600">
          No events found for this run.
        </div>
      )}

      {auditBundle && (
        <div className="border-t px-4 py-3">
          <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1">
            Audit Bundle
          </h4>
          <pre className="text-xs bg-gray-100 dark:bg-gray-800 rounded p-2 overflow-x-auto text-gray-700 dark:text-gray-300">
            {JSON.stringify(auditBundle.bundle, null, 2)}
          </pre>
        </div>
      )}

      {(error || apiError.hasError) && (
        <div className="border-t px-4 py-3 text-xs text-red-600 dark:text-red-400">
          {error || apiError.error?.message}
        </div>
      )}
    </div>
  );
}
