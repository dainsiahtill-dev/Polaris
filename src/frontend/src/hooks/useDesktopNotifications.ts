/**
 * useDesktopNotifications - Hook for system desktop notifications
 *
 * This hook provides a wrapper around the Electron notification API
 * for showing native desktop notifications.
 */

import { useCallback } from 'react';
import { devLogger } from '@/app/utils/devLogger';

export interface DesktopNotificationOptions {
  title?: string;
  body: string;
  silent?: boolean;
}

export interface DesktopNotificationResult {
  ok: boolean;
  error?: string | null;
}

/**
 * Hook to show native desktop notifications via Electron
 */
export function useDesktopNotifications() {
  const show = useCallback(async (options: DesktopNotificationOptions): Promise<DesktopNotificationResult> => {
    // Check if running in Electron renderer
    if (typeof window === 'undefined' || !window.polaris?.notification) {
      devLogger.warn('[useDesktopNotifications] Not running in Electron or notification API unavailable');
      return { ok: false, error: 'Notification API not available' };
    }

    try {
      const result = await window.polaris.notification.show({
        title: options.title ?? 'Polaris',
        body: options.body,
        silent: options.silent ?? false,
      });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      devLogger.error('[useDesktopNotifications] Failed to show notification:', message);
      return { ok: false, error: message };
    }
  }, []);

  const success = useCallback((message: string, title?: string) => {
    return show({ title: title ?? 'Success', body: message });
  }, [show]);

  const error = useCallback((message: string, title?: string) => {
    return show({ title: title ?? 'Error', body: message });
  }, [show]);

  const warning = useCallback((message: string, title?: string) => {
    return show({ title: title ?? 'Warning', body: message });
  }, [show]);

  const info = useCallback((message: string, title?: string) => {
    return show({ title: title ?? 'Info', body: message });
  }, [show]);

  return {
    show,
    success,
    error,
    warning,
    info,
  };
}

export default useDesktopNotifications;
