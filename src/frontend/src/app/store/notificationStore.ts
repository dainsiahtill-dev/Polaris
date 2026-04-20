/**
 * Notification Store - Zustand 状态管理
 * 集中管理通知相关的用户偏好设置
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// ============================================================================
// Types
// ============================================================================

export interface NotificationState {
  // 通知设置
  desktopNotifications: boolean;
  notificationSound: boolean;
  notificationVolume: number; // 0-100
  soundEnabled: boolean;

  // Toast 通知队列
  toasts: Array<{
    id: string;
    type: 'info' | 'success' | 'warning' | 'error';
    message: string;
    duration?: number;
    title?: string;
  }>;

  // 通知偏好
  showPmNotifications: boolean;
  showDirectorNotifications: boolean;
  showTaskNotifications: boolean;
  showErrorNotifications: boolean;
  showSuccessNotifications: boolean;

  // 免打扰模式
  dndEnabled: boolean;
  dndStartTime: string; // HH:mm
  dndEndTime: string; // HH:mm
}

export interface NotificationActions {
  // 基础设置
  setDesktopNotifications: (enabled: boolean) => void;
  setNotificationSound: (enabled: boolean) => void;
  setNotificationVolume: (volume: number) => void;
  setSoundEnabled: (enabled: boolean) => void;

  // Toast 操作
  addToast: (toast: Omit<NotificationState['toasts'][0], 'id'>) => string;
  removeToast: (id: string) => void;
  clearToasts: () => void;

  // 通知偏好
  setShowPmNotifications: (show: boolean) => void;
  setShowDirectorNotifications: (show: boolean) => void;
  setShowTaskNotifications: (show: boolean) => void;
  setShowErrorNotifications: (show: boolean) => void;
  setShowSuccessNotifications: (show: boolean) => void;

  // 免打扰模式
  setDndEnabled: (enabled: boolean) => void;
  setDndTimeRange: (start: string, end: string) => void;

  // 请求权限
  requestDesktopPermission: () => Promise<boolean>;
}

// ============================================================================
// Constants
// ============================================================================

const NOTIFICATION_STORAGE_KEY = 'polaris:notifications';
const DEFAULT_TOAST_DURATION = 5000;
const MAX_TOASTS = 10;

// ============================================================================
// Helper Functions
// ============================================================================

function generateToastId(): string {
  return `toast-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function clampVolume(volume: number): number {
  return Math.max(0, Math.min(100, volume));
}

// ============================================================================
// Store Creation
// ============================================================================

export const useNotificationStore = create<NotificationState & NotificationActions>()(
  persist(
    (set, get) => ({
      // ============ 初始状态 ============
      desktopNotifications: false,
      notificationSound: true,
      notificationVolume: 80,
      soundEnabled: true,

      toasts: [],

      showPmNotifications: true,
      showDirectorNotifications: true,
      showTaskNotifications: true,
      showErrorNotifications: true,
      showSuccessNotifications: true,

      dndEnabled: false,
      dndStartTime: '22:00',
      dndEndTime: '08:00',

      // ============ 基础设置 ============
      setDesktopNotifications: (enabled) => set({ desktopNotifications: enabled }),
      setNotificationSound: (enabled) => set({ notificationSound: enabled }),
      setNotificationVolume: (volume) => set({ notificationVolume: clampVolume(volume) }),
      setSoundEnabled: (enabled) => set({ soundEnabled: enabled }),

      // ============ Toast 操作 ============
      addToast: (toast) => {
        const id = generateToastId();
        const newToast = { ...toast, id };
        const duration = toast.duration ?? DEFAULT_TOAST_DURATION;

        set((state) => ({
          // 限制最大 Toast 数量
          toasts: [...state.toasts.slice(-(MAX_TOASTS - 1)), newToast],
        }));

        // 自动移除
        if (duration > 0) {
          setTimeout(() => {
            get().removeToast(id);
          }, duration);
        }

        return id;
      },

      removeToast: (id) => set((state) => ({
        toasts: state.toasts.filter((t) => t.id !== id),
      })),

      clearToasts: () => set({ toasts: [] }),

      // ============ 通知偏好 ============
      setShowPmNotifications: (show) => set({ showPmNotifications: show }),
      setShowDirectorNotifications: (show) => set({ showDirectorNotifications: show }),
      setShowTaskNotifications: (show) => set({ showTaskNotifications: show }),
      setShowErrorNotifications: (show) => set({ showErrorNotifications: show }),
      setShowSuccessNotifications: (show) => set({ showSuccessNotifications: show }),

      // ============ 免打扰模式 ============
      setDndEnabled: (enabled) => set({ dndEnabled: enabled }),
      setDndTimeRange: (start, end) => set({ dndStartTime: start, dndEndTime: end }),

      // ============ 权限请求 ============
      requestDesktopPermission: async () => {
        if (!('Notification' in window)) {
          return false;
        }

        if (Notification.permission === 'granted') {
          set({ desktopNotifications: true });
          return true;
        }

        if (Notification.permission === 'denied') {
          return false;
        }

        try {
          const permission = await Notification.requestPermission();
          const granted = permission === 'granted';
          set({ desktopNotifications: granted });
          return granted;
        } catch {
          return false;
        }
      },
    }),
    {
      name: NOTIFICATION_STORAGE_KEY,
      partialize: (state) => ({
        // 只持久化用户偏好，不持久化 Toast
        desktopNotifications: state.desktopNotifications,
        notificationSound: state.notificationSound,
        notificationVolume: state.notificationVolume,
        soundEnabled: state.soundEnabled,
        showPmNotifications: state.showPmNotifications,
        showDirectorNotifications: state.showDirectorNotifications,
        showTaskNotifications: state.showTaskNotifications,
        showErrorNotifications: state.showErrorNotifications,
        showSuccessNotifications: state.showSuccessNotifications,
        dndEnabled: state.dndEnabled,
        dndStartTime: state.dndStartTime,
        dndEndTime: state.dndEndTime,
      }),
    }
  )
);

// ============================================================================
// Selector Hooks
// ============================================================================

/** Toast 列表和操作 */
export const useToasts = () => useNotificationStore((state) => ({
  toasts: state.toasts,
  addToast: state.addToast,
  removeToast: state.removeToast,
  clearToasts: state.clearToasts,
}));

/** 通知设置 */
export const useNotificationSettings = () => useNotificationStore((state) => ({
  desktopNotifications: state.desktopNotifications,
  notificationSound: state.notificationSound,
  notificationVolume: state.notificationVolume,
  soundEnabled: state.soundEnabled,
  setDesktopNotifications: state.setDesktopNotifications,
  setNotificationSound: state.setNotificationSound,
  setNotificationVolume: state.setNotificationVolume,
  setSoundEnabled: state.setSoundEnabled,
}));

/** 通知偏好 */
export const useNotificationPreferences = () => useNotificationStore((state) => ({
  showPmNotifications: state.showPmNotifications,
  showDirectorNotifications: state.showDirectorNotifications,
  showTaskNotifications: state.showTaskNotifications,
  showErrorNotifications: state.showErrorNotifications,
  showSuccessNotifications: state.showSuccessNotifications,
  setShowPmNotifications: state.setShowPmNotifications,
  setShowDirectorNotifications: state.setShowDirectorNotifications,
  setShowTaskNotifications: state.setShowTaskNotifications,
  setShowErrorNotifications: state.setShowErrorNotifications,
  setShowSuccessNotifications: state.setShowSuccessNotifications,
}));

/** 免打扰模式 */
export const useDndSettings = () => useNotificationStore((state) => ({
  dndEnabled: state.dndEnabled,
  dndStartTime: state.dndStartTime,
  dndEndTime: state.dndEndTime,
  setDndEnabled: state.setDndEnabled,
  setDndTimeRange: state.setDndTimeRange,
}));

/** 桌面通知权限状态 */
export const useDesktopNotificationPermission = () => {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission;
};
