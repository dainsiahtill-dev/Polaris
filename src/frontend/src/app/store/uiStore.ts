/**
 * UI Store - Zustand 状态管理
 * 集中管理所有 UI 相关状态，消除 Context 嵌套
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// ============================================================================
// Types
// ============================================================================

export type RightPanelTab = 'context' | 'memo' | 'memory' | 'snapshot';

export interface UIState {
  // Modal 状态
  isSettingsOpen: boolean;
  isLogsOpen: boolean;
  isHistoryOpen: boolean;
  isReportDrawerOpen: boolean;

  // 布局状态
  sidebarCollapsed: boolean;
  rightPanelCollapsed: boolean;
  rightPanelTab: RightPanelTab;

  // 主题与显示
  theme: 'dark' | 'light' | 'system';
  compactMode: boolean;
  fontSize: number;

  // 全局加载状态
  globalLoading: boolean;
  loadingMessage: string | null;

  // Toast 通知队列
  toasts: Array<{
    id: string;
    type: 'info' | 'success' | 'warning' | 'error';
    message: string;
    duration?: number;
    _timerId?: ReturnType<typeof setTimeout>;
  }>;
}

export interface UIActions {
  // Modal 操作
  openSettings: () => void;
  closeSettings: () => void;
  toggleSettings: () => void;

  openLogs: () => void;
  closeLogs: () => void;
  toggleLogs: () => void;

  openHistory: () => void;
  closeHistory: () => void;
  toggleHistory: () => void;

  openReportDrawer: () => void;
  closeReportDrawer: () => void;

  // 布局操作
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleRightPanel: () => void;
  setRightPanelCollapsed: (collapsed: boolean) => void;
  setRightPanelTab: (tab: RightPanelTab) => void;

  // 主题操作
  setTheme: (theme: UIState['theme']) => void;
  setCompactMode: (compact: boolean) => void;
  setFontSize: (size: number) => void;

  // 全局加载
  setGlobalLoading: (loading: boolean, message?: string) => void;

  // Toast 操作
  addToast: (toast: Omit<UIState['toasts'][0], 'id'>) => void;
  removeToast: (id: string) => void;
  clearToasts: () => void;
}

// ============================================================================
// Constants
// ============================================================================

const UI_STORAGE_KEY = 'polaris:ui';
const DEFAULT_FONT_SIZE = 14;
const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 24;
const DEFAULT_TOAST_DURATION = 5000;

// ============================================================================
// Store Creation
// ============================================================================

export const useUIStore = create<UIState & UIActions>()(
  persist(
    (set, get) => ({
      // ============ 初始状态 ============
      isSettingsOpen: false,
      isLogsOpen: false,
      isHistoryOpen: false,
      isReportDrawerOpen: false,

      sidebarCollapsed: false,
      rightPanelCollapsed: false,
      rightPanelTab: 'context',

      theme: 'dark',
      compactMode: false,
      fontSize: DEFAULT_FONT_SIZE,

      globalLoading: false,
      loadingMessage: null,

      toasts: [],

      // ============ Modal 操作 ============
      openSettings: () => set({ isSettingsOpen: true }),
      closeSettings: () => set({ isSettingsOpen: false }),
      toggleSettings: () => set((s) => ({ isSettingsOpen: !s.isSettingsOpen })),

      openLogs: () => set({ isLogsOpen: true }),
      closeLogs: () => set({ isLogsOpen: false }),
      toggleLogs: () => set((s) => ({ isLogsOpen: !s.isLogsOpen })),

      openHistory: () => set({ isHistoryOpen: true }),
      closeHistory: () => set({ isHistoryOpen: false }),
      toggleHistory: () => set((s) => ({ isHistoryOpen: !s.isHistoryOpen })),

      openReportDrawer: () => set({ isReportDrawerOpen: true }),
      closeReportDrawer: () => set({ isReportDrawerOpen: false }),

      // ============ 布局操作 ============
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleRightPanel: () => set((s) => ({ rightPanelCollapsed: !s.rightPanelCollapsed })),
      setRightPanelCollapsed: (collapsed) => set({ rightPanelCollapsed: collapsed }),
      setRightPanelTab: (tab) => set({ rightPanelTab: tab }),

      // ============ 主题操作 ============
      setTheme: (theme) => set({ theme }),
      setCompactMode: (compactMode) => set({ compactMode }),
      setFontSize: (fontSize) => set({
        fontSize: Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, fontSize)),
      }),

      // ============ 全局加载 ============
      setGlobalLoading: (loading, message) => set({
        globalLoading: loading,
        loadingMessage: message ?? null,
      }),

      // ============ Toast 操作 ============
      addToast: (toast) => {
        const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        const newToast = { ...toast, id };
        const duration = toast.duration ?? DEFAULT_TOAST_DURATION;

        set((s) => ({
          toasts: [...s.toasts, newToast],
        }));

        // 自动移除 - 存储 timer ID 以便在 removeToast 中清理
        if (duration > 0) {
          const timerId = setTimeout(() => {
            get().removeToast(id);
          }, duration);
          // 存储 timer ID 到 toast 中以便后续清理
          set((s) => ({
            toasts: s.toasts.map((t) =>
              t.id === id ? { ...t, _timerId: timerId } : t
            ),
          }));
        }

        return id;
      },

      removeToast: (id) => set((s) => {
        const toast = s.toasts.find((t) => t.id === id);
        if (toast && '_timerId' in toast && typeof toast._timerId === 'number') {
          clearTimeout(toast._timerId);
        }
        return {
          toasts: s.toasts.filter((t) => t.id !== id),
        };
      }),

      clearToasts: () => set({ toasts: [] }),
    }),
    {
      name: UI_STORAGE_KEY,
      partialize: (state) => ({
        // 只持久化 UI 首选项，不持久化临时状态
        sidebarCollapsed: state.sidebarCollapsed,
        rightPanelCollapsed: state.rightPanelCollapsed,
        theme: state.theme,
        compactMode: state.compactMode,
        fontSize: state.fontSize,
      }),
    }
  )
);

// ============================================================================
// 便捷 Selector Hooks
// ============================================================================

/** 只获取主题相关状态 */
export const useThemeSettings = () => useUIStore((state) => ({
  theme: state.theme,
  compactMode: state.compactMode,
  fontSize: state.fontSize,
  setTheme: state.setTheme,
  setCompactMode: state.setCompactMode,
  setFontSize: state.setFontSize,
}));

/** 只获取布局状态 */
export const useLayoutState = () => useUIStore((state) => ({
  sidebarCollapsed: state.sidebarCollapsed,
  rightPanelCollapsed: state.rightPanelCollapsed,
  rightPanelTab: state.rightPanelTab,
  toggleSidebar: state.toggleSidebar,
  toggleRightPanel: state.toggleRightPanel,
  setRightPanelTab: state.setRightPanelTab,
}));

/** 只获取 Modal 状态 */
export const useModalState = () => useUIStore((state) => ({
  isSettingsOpen: state.isSettingsOpen,
  isLogsOpen: state.isLogsOpen,
  isHistoryOpen: state.isHistoryOpen,
  isReportDrawerOpen: state.isReportDrawerOpen,
  openSettings: state.openSettings,
  closeSettings: state.closeSettings,
  toggleSettings: state.toggleSettings,
  openLogs: state.openLogs,
  closeLogs: state.closeLogs,
  openHistory: state.openHistory,
  closeHistory: state.closeHistory,
  openReportDrawer: state.openReportDrawer,
  closeReportDrawer: state.closeReportDrawer,
}));

/** Toast 操作 */
export const useToasts = () => useUIStore((state) => ({
  toasts: state.toasts,
  addToast: state.addToast,
  removeToast: state.removeToast,
  clearToasts: state.clearToasts,
}));
