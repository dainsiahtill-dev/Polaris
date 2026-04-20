import { useEffect, useState, useRef } from 'react';
import { CheckCircle, XCircle, AlertTriangle, Info, Loader2, ExternalLink } from 'lucide-react';

export type NotificationType = 'success' | 'error' | 'warning' | 'info' | 'loading';

export interface Notification {
  id: string;
  type: NotificationType;
  title?: string;
  message: string;
  duration?: number;
  actions?: Array<{
    label: string;
    onClick: () => void;
  }>;
  progress?: boolean;
  persist?: boolean;
  details?: string;
}

interface NotificationManagerProps {
  notifications: Notification[];
  onDismiss: (id: string) => void;
  maxVisible?: number;
}

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

export function EnhancedNotificationManager({
  notifications,
  onDismiss,
  maxVisible = 5,
}: NotificationManagerProps) {
  const [progress, setProgress] = useState<Record<string, number>>({});
  const timerRefs = useRef<Map<string, NodeJS.Timeout>>(new Map());

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
      } else if (notification.duration && !notification.persist) {
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

  if (notifications.length === 0) return null;

  const visibleNotifications = notifications.slice(-maxVisible);

  return (
    <div className="fixed top-4 right-4 z-50 space-y-2 max-w-sm">
      {visibleNotifications.map((notification) => {
        const Icon = icons[notification.type];
        const progressWidth = progress[notification.id] || 0;

        return (
          <div
            key={notification.id}
            className={`
              relative p-4 rounded-lg border shadow-lg backdrop-blur-sm
              ${colors[notification.type]}
              ${notification.type === 'loading' ? 'animate-pulse' : ''}
              transition-all duration-300 ease-in-out
              transform hover:scale-105
            `}
          >
            {notification.progress && (
              <div className="absolute top-0 left-0 h-1 bg-current opacity-20 rounded-t-lg">
                <div
                  className="h-full bg-current rounded-t-lg transition-all duration-75"
                  style={{ width: `${progressWidth}%` }}
                />
              </div>
            )}
            
            <div className="flex gap-3">
              <div className="flex-shrink-0">
                <Icon className={`h-5 w-5 ${notification.type === 'loading' ? 'animate-spin' : ''}`} />
              </div>
              
              <div className="flex-1 min-w-0">
                {notification.title && (
                  <h3 className="text-sm font-semibold mb-1">
                    {notification.title}
                  </h3>
                )}
                <p className="text-sm leading-relaxed">
                  {notification.message}
                </p>
                
                {notification.details && (
                  <details className="mt-2">
                    <summary className="text-xs cursor-pointer hover:text-gray-300 transition-colors flex items-center gap-1">
                      <ExternalLink className="h-3 w-3" />
                      查看详情
                    </summary>
                    <div className="mt-2 text-xs text-gray-400 bg-black/20 rounded p-2 max-h-32 overflow-y-auto">
                      {notification.details}
                    </div>
                  </details>
                )}
                
                {notification.actions && notification.actions.length > 0 && (
                  <div className="mt-3 flex gap-2">
                    {notification.actions.map((action, index) => (
                      <button
                        key={index}
                        onClick={() => {
                          action.onClick();
                          onDismiss(notification.id);
                        }}
                        className="text-xs px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 transition-colors"
                      >
                        {action.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              
              <button
                onClick={() => onDismiss(notification.id)}
                className="flex-shrink-0 text-gray-400 hover:text-gray-200 transition-colors"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
