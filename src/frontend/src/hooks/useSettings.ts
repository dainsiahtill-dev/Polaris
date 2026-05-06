/**
 * useSettings - Settings management hook with React Query caching
 *
 * Provides centralized settings management with automatic caching,
 * request deduplication, and background refetch.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { settingsService } from '@/services/api';
import type { BackendSettings } from '@/app/types/appContracts';
import { QueryKeys } from '@/lib/queryClient';

const SETTINGS_QUERY_KEY = QueryKeys.settings();

export interface UseSettingsOptions {
  /** Enable automatic loading on mount (default: true) */
  autoLoad?: boolean;
  /** Stale time in milliseconds (default: 5 minutes) */
  staleTime?: number;
}

/**
 * Hook for fetching and managing backend settings with React Query
 *
 * @example
 * ```tsx
 * const { settings, isLoading, error, refetch } = useSettings();
 * ```
 */
export function useSettings(options: UseSettingsOptions = {}) {
  const { autoLoad = true, staleTime } = options;

  const queryClient = useQueryClient();
  const latestUpdateIdRef = useRef(0);
  const stableSettingsRef = useRef<BackendSettings | null>(null);

  // Query for settings
  const {
    data,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: async () => {
      const result = await settingsService.get();
      if (!result.ok) {
        throw new Error(result.error || 'Failed to load settings');
      }
      return result.data as BackendSettings;
    },
    enabled: autoLoad,
    staleTime: staleTime ?? 5 * 60 * 1000,
  });

  // Mutation for updating settings
  const updateMutation = useMutation({
    mutationFn: async (updates: Partial<BackendSettings>) => {
      const result = await settingsService.update(updates);
      if (!result.ok) {
        throw new Error(result.error || 'Failed to update settings');
      }
      return result.data as BackendSettings;
    },
    onSuccess: (newSettings) => {
      stableSettingsRef.current = newSettings;
      // Update the settings query cache with new data
      queryClient.setQueryData<BackendSettings>(SETTINGS_QUERY_KEY, newSettings);
    },
  });

  useEffect(() => {
    if (data && !updateMutation.isPending) {
      stableSettingsRef.current = data;
    }
  }, [data, updateMutation.isPending]);

  /**
   * Force reload settings (bypass cache)
   */
  const load = async () => {
    await queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    return getCachedSettings();
  };

  /**
   * Get current settings from cache
   * This reads directly from cache for immediate access after updates
   */
  const getCachedSettings = () => {
    return queryClient.getQueryData<BackendSettings>(SETTINGS_QUERY_KEY) ?? null;
  };

  /**
   * Update settings - optimistically updates cache immediately
   * for synchronous UI feedback, then syncs with server response
   */
  const update = async (updates: Partial<BackendSettings>) => {
    const updateId = ++latestUpdateIdRef.current;
    // Optimistically update the cache with merged updates
    const currentData = queryClient.getQueryData<BackendSettings>(SETTINGS_QUERY_KEY);
    const optimisticData = { ...currentData, ...updates } as BackendSettings;
    queryClient.setQueryData<BackendSettings>(SETTINGS_QUERY_KEY, optimisticData);

    // Then perform the actual mutation
    try {
      const result = await updateMutation.mutateAsync(updates);
      return result;
    } catch (error) {
      // Only the latest failed request can rollback to avoid stale failures
      // overwriting newer successful updates.
      if (updateId === latestUpdateIdRef.current) {
        queryClient.setQueryData<BackendSettings>(
          SETTINGS_QUERY_KEY,
          stableSettingsRef.current ?? currentData ?? undefined
        );
      }
      throw error;
    }
  };

  return {
    /** Current settings data (from React Query cache) */
    settings: getCachedSettings(),
    /** Loading state (initial fetch) */
    loading: isLoading,
    /** Fetching state (including background refetch) */
    isFetching,
    /** Error message if fetch failed */
    error: error instanceof Error ? error.message : error as string | null,
    /** Manual refetch function */
    refetch,
    /** Update settings function */
    update,
    /** Force reload settings */
    load,
    /** Direct setter for imperative updates */
    setSettings: (newSettings: BackendSettings | null) => {
      queryClient.setQueryData<BackendSettings>(SETTINGS_QUERY_KEY, newSettings ?? undefined);
    },
    /** Invalidate and refetch settings */
    invalidate: () => {
      return queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    },
  };
}
