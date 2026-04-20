import { useState, useCallback } from 'react';

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info' | 'loading';
  title?: string;
  message: string;
  duration?: number;
  actions?: Array<{ label: string; onClick: () => void }>;
  progress?: boolean;
  persist?: boolean;
}

export function useNotifications() {
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const add = useCallback((notification: Omit<Notification, 'id'>): string => {
    const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setNotifications(prev => [...prev, { ...notification, id }]);

    if (!notification.persist && notification.duration !== 0) {
      setTimeout(() => {
        remove(id);
      }, notification.duration || 5000);
    }

    return id;
  }, []);

  const remove = useCallback((id: string) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  const clear = useCallback(() => {
    setNotifications([]);
  }, []);

  const success = useCallback((message: string, title?: string) => {
    return add({ type: 'success', message, title });
  }, [add]);

  const error = useCallback((message: string, title?: string) => {
    return add({ type: 'error', message, title, duration: 10000 });
  }, [add]);

  const warning = useCallback((message: string, title?: string) => {
    return add({ type: 'warning', message, title });
  }, [add]);

  const info = useCallback((message: string, title?: string) => {
    return add({ type: 'info', message, title });
  }, [add]);

  const loading = useCallback((message: string, title?: string) => {
    return add({ type: 'loading', message, title, persist: true });
  }, [add]);

  return {
    notifications,
    add,
    remove,
    clear,
    success,
    error,
    warning,
    info,
    loading,
  };
}
