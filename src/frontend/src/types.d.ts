export { };

declare global {
  interface Window {
    polaris?: {
      getBackendInfo: () => Promise<{
        port: number | null;
        token: string | null;
        baseUrl: string | null;
        pid: number | null;
      }>;
      getBackendStatus?: () => Promise<{
        state: string;
        ready: boolean;
        restarts: number;
        lastError: string;
        lastExitCode: number | null;
        info: {
          port: number | null;
          token: string | null;
          baseUrl: string | null;
          pid: number | null;
        };
      }>;
      pickWorkspace: (options?: { defaultPath?: string }) => Promise<string | null>;
      openPath: (targetPath: string) => Promise<{ ok: boolean; error?: string | null }>;
      secrets?: {
        available: () => Promise<{ ok: boolean; available: boolean; error?: string | null }>;
        get: (key: string) => Promise<{ ok: boolean; value?: string | null; error?: string | null }>;
        set: (key: string, value: string) => Promise<{ ok: boolean; error?: string | null }>;
        remove: (key: string) => Promise<{ ok: boolean; error?: string | null }>;
      };
      pty?: {
        start: (payload: {
          command: string;
          args?: string[];
          cwd?: string;
          env?: Record<string, string>;
          cols?: number;
          rows?: number;
          use_conpty?: boolean;
        }) => Promise<{ ok: boolean; id?: string; error?: string | null }>;
        write: (id: string, data: string) => Promise<{ ok: boolean; error?: string | null }>;
        resize: (id: string, cols: number, rows: number) => Promise<{ ok: boolean; error?: string | null }>;
        close: (id: string) => Promise<{ ok: boolean; error?: string | null }>;
        onData: (handler: (payload: { id: string; data: string }) => void) => () => void;
        onExit: (handler: (payload: { id: string; exitCode?: number; signal?: number }) => void) => () => void;
      };
      windowControl?: {
        getState: () => Promise<{ maximized: boolean } | null>;
        minimize: () => void;
        maximize: () => void;
        close: () => void;
      };
      notification?: {
        show: (options: {
          title?: string;
          body?: string;
          silent?: boolean;
        }) => Promise<{ ok: boolean; error?: string | null }>;
      };
      onAction?: (handler: (payload: { type: string;[key: string]: unknown }) => void) => () => void;
    };
  }
}
