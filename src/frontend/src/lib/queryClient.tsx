/**
 * React Query Client Configuration
 *
 * Centralized cache layer for Polaris frontend.
 * Provides:
 * - Request deduplication
 * - Automatic request cancellation via AbortController
 * - Configurable stale time and garbage collection
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

/**
 * Default query client configuration
 * - staleTime: 5 minutes - data is considered fresh for 5 minutes
 * - gcTime: 30 minutes - unused cached data is garbage collected after 30 minutes
 * - retry: 2 - retry failed requests up to 2 times
 * - refetchOnWindowFocus: false - don't refetch when window regains focus
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      gcTime: 30 * 60 * 1000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});

/**
 * Query provider component
 * Wrap your app with this to enable React Query
 */
export function QueryProvider({ children }: { children: ReactNode }): React.ReactElement {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}

/**
 * Query keys registry for type-safe query key management
 */
export const QueryKeys = {
  /** Backend settings */
  settings: () => ['settings'] as const,

  /** Factory runs */
  factoryRuns: (limit?: number) => ['factory', 'runs', limit] as const,
  factoryRun: (runId: string) => ['factory', 'run', runId] as const,

  /** Snapshot state */
  snapshot: () => ['snapshot'] as const,

  /** LLM status */
  llmStatus: () => ['llm', 'status'] as const,

  /** Resident status */
  residentStatus: (workspace: string) => ['resident', 'status', workspace] as const,

  /** Usage stats */
  usageStats: (workspace: string) => ['usage', 'stats', workspace] as const,

  /** File content */
  fileContent: (path: string) => ['file', 'content', path] as const,

  /** Memos list */
  memos: (limit?: number) => ['memos', 'list', limit] as const,
} as const;
