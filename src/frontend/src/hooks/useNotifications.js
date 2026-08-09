import { useState, useCallback } from 'react';
export function useNotifications() {
    const [notifications, setNotifications] = useState([]);
    const add = useCallback((notification) => {
        const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        setNotifications(prev => [...prev, { ...notification, id }]);
        if (!notification.persist && notification.duration !== 0) {
            setTimeout(() => {
                remove(id);
            }, notification.duration || 5000);
        }
        return id;
    }, []);
    const remove = useCallback((id) => {
        setNotifications(prev => prev.filter(n => n.id !== id));
    }, []);
    const clear = useCallback(() => {
        setNotifications([]);
    }, []);
    const success = useCallback((message, title) => {
        return add({ type: 'success', message, title });
    }, [add]);
    const error = useCallback((message, title) => {
        return add({ type: 'error', message, title, duration: 10000 });
    }, [add]);
    const warning = useCallback((message, title) => {
        return add({ type: 'warning', message, title });
    }, [add]);
    const info = useCallback((message, title) => {
        return add({ type: 'info', message, title });
    }, [add]);
    const loading = useCallback((message, title) => {
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
