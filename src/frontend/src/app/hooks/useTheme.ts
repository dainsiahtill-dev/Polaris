/**
 * useTheme - 主题管理 Hook
 * 支持亮色/暗色/系统自动主题切换
 */
import { useEffect } from 'react';
import { persist, createJSONStorage } from 'zustand/middleware';
import { create } from 'zustand';

export type Theme = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const THEME_STORAGE_KEY = 'polaris:ui:theme';

const getSystemTheme = (): ResolvedTheme => {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

const resolveTheme = (theme: Theme): ResolvedTheme => {
  if (theme === 'system') {
    return getSystemTheme();
  }
  return theme;
};

const applyThemeToDOM = (resolvedTheme: ResolvedTheme): void => {
  const root = document.documentElement;
  if (resolvedTheme === 'dark') {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
};

const zustandStorage = {
  getItem: (name: string): string | null => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name: string, value: string): void => {
    try {
      localStorage.setItem(name, value);
    } catch {
      // ignore
    }
  },
  removeItem: (name: string): void => {
    try {
      localStorage.removeItem(name);
    } catch {
      // ignore
    }
  },
};

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => {
      const applyTheme = (theme: Theme) => {
        const resolvedTheme = resolveTheme(theme);
        applyThemeToDOM(resolvedTheme);
        set({ theme, resolvedTheme });
      };

      return {
        theme: 'dark',
        resolvedTheme: 'dark',
        setTheme: applyTheme,
      };
    },
    {
      name: THEME_STORAGE_KEY,
      storage: createJSONStorage(() => zustandStorage),
      onRehydrateStorage: () => {
        return (state) => {
          if (state) {
            const resolved = resolveTheme(state.theme);
            state.resolvedTheme = resolved;
            applyThemeToDOM(resolved);
          }
        };
      },
    }
  )
);

export function useTheme() {
  const theme = useThemeStore((s) => s.theme);
  const resolvedTheme = useThemeStore((s) => s.resolvedTheme);
  const setTheme = useThemeStore((s) => s.setTheme);

  // 监听系统主题变化
  useEffect(() => {
    if (theme !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      const resolved = getSystemTheme();
      applyThemeToDOM(resolved);
      useThemeStore.setState({ resolvedTheme: resolved });
    };

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [theme]);

  // 初始化时应用主题
  useEffect(() => {
    const resolved = resolveTheme(theme);
    applyThemeToDOM(resolved);
    useThemeStore.setState({ resolvedTheme: resolved });
  }, []);

  return {
    theme,
    resolvedTheme,
    setTheme,
  };
}
