import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState, useRef } from 'react';
import { CheckCircle, XCircle, AlertTriangle, Info, Loader2, ExternalLink } from 'lucide-react';
const icons = {
    success: CheckCircle,
    error: XCircle,
    warning: AlertTriangle,
    info: Info,
    loading: Loader2,
};
const colors = {
    success: 'bg-green-500/10 border-green-500/20 text-green-400',
    error: 'bg-red-500/10 border-red-500/20 text-red-400',
    warning: 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400',
    info: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
    loading: 'bg-gray-500/10 border-gray-500/20 text-gray-400',
};
export function EnhancedNotificationManager({ notifications, onDismiss, maxVisible = 5, }) {
    const [progress, setProgress] = useState({});
    const timerRefs = useRef(new Map());
    useEffect(() => {
        // Clean up all existing timers first
        timerRefs.current.forEach((timer) => {
            clearInterval(timer);
            clearTimeout(timer);
        });
        timerRefs.current.clear();
        notifications.forEach((notification) => {
            if (notification.progress && notification.duration) {
                const duration = notification.duration;
                const startTime = Date.now();
                // UI-only progress countdown for toast lifetime; not a data refresh loop.
                const interval = setInterval(() => {
                    const elapsed = Date.now() - startTime;
                    const newProgress = Math.max(0, 100 - (elapsed / duration) * 100);
                    setProgress((prev) => ({
                        ...prev,
                        [notification.id]: newProgress,
                    }));
                    if (newProgress <= 0) {
                        clearInterval(interval);
                        timerRefs.current.delete(notification.id);
                        if (!notification.persist) {
                            onDismiss(notification.id);
                        }
                    }
                }, 50);
                timerRefs.current.set(notification.id, interval);
            }
            else if (notification.duration && !notification.persist) {
                const duration = notification.duration;
                const timeout = setTimeout(() => {
                    timerRefs.current.delete(notification.id);
                    onDismiss(notification.id);
                }, duration);
                timerRefs.current.set(notification.id, timeout);
            }
        });
        // Cleanup all timers on unmount or when notifications change
        return () => {
            timerRefs.current.forEach((timer) => {
                clearInterval(timer);
                clearTimeout(timer);
            });
            timerRefs.current.clear();
        };
    }, [notifications, onDismiss]);
    if (notifications.length === 0)
        return null;
    const visibleNotifications = notifications.slice(-maxVisible);
    return (_jsx("div", { className: "fixed top-4 right-4 z-50 space-y-2 max-w-sm", children: visibleNotifications.map((notification) => {
            const Icon = icons[notification.type];
            const progressWidth = progress[notification.id] || 0;
            return (_jsxs("div", { className: `
              relative p-4 rounded-lg border shadow-lg backdrop-blur-sm
              ${colors[notification.type]}
              ${notification.type === 'loading' ? 'animate-pulse' : ''}
              transition-all duration-300 ease-in-out
              transform hover:scale-105
            `, children: [notification.progress && (_jsx("div", { className: "absolute top-0 left-0 h-1 bg-current opacity-20 rounded-t-lg", children: _jsx("div", { className: "h-full bg-current rounded-t-lg transition-all duration-75", style: { width: `${progressWidth}%` } }) })), _jsxs("div", { className: "flex gap-3", children: [_jsx("div", { className: "flex-shrink-0", children: _jsx(Icon, { className: `h-5 w-5 ${notification.type === 'loading' ? 'animate-spin' : ''}` }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [notification.title && (_jsx("h3", { className: "text-sm font-semibold mb-1", children: notification.title })), _jsx("p", { className: "text-sm leading-relaxed", children: notification.message }), notification.details && (_jsxs("details", { className: "mt-2", children: [_jsxs("summary", { className: "text-xs cursor-pointer hover:text-gray-300 transition-colors flex items-center gap-1", children: [_jsx(ExternalLink, { className: "h-3 w-3" }), "\u67E5\u770B\u8BE6\u60C5"] }), _jsx("div", { className: "mt-2 text-xs text-gray-400 bg-black/20 rounded p-2 max-h-32 overflow-y-auto", children: notification.details })] })), notification.actions && notification.actions.length > 0 && (_jsx("div", { className: "mt-3 flex gap-2", children: notification.actions.map((action, index) => (_jsx("button", { onClick: () => {
                                                action.onClick();
                                                onDismiss(notification.id);
                                            }, className: "text-xs px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 transition-colors", children: action.label }, index))) }))] }), _jsx("button", { onClick: () => onDismiss(notification.id), className: "flex-shrink-0 text-gray-400 hover:text-gray-200 transition-colors", children: _jsx("svg", { className: "h-4 w-4", fill: "currentColor", viewBox: "0 0 20 20", children: _jsx("path", { fillRule: "evenodd", d: "M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z", clipRule: "evenodd" }) }) })] })] }, notification.id));
        }) }));
}
