import { jsx as _jsx } from "react/jsx-runtime";
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
export function QueryProvider({ children }) {
    return (_jsx(QueryClientProvider, { client: queryClient, children: children }));
}
/**
 * Query keys registry for type-safe query key management
 */
export const QueryKeys = {
    /** Backend settings */
    settings: () => ['settings'],
    /** Factory runs */
    factoryRuns: (limit) => ['factory', 'runs', limit],
    factoryRun: (runId) => ['factory', 'run', runId],
    /** Snapshot state */
    snapshot: () => ['snapshot'],
    /** LLM status */
    llmStatus: () => ['llm', 'status'],
    /** Resident status */
    residentStatus: (workspace) => ['resident', 'status', workspace],
    /** Usage stats */
    usageStats: (workspace) => ['usage', 'stats', workspace],
    /** File content */
    fileContent: (path) => ['file', 'content', path],
    /** Memos list */
    memos: (limit) => ['memos', 'list', limit],
};
