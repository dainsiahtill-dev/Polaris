import { useState, useCallback, useRef, useEffect } from 'react';

export interface TerminalSession {
  id: string;
  cols: number;
  rows: number;
}

export function useTerminal() {
  const [session, setSession] = useState<TerminalSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);

  const createSession = useCallback(async (options: { cwd?: string; env?: Record<string, string>; cols?: number; rows?: number } = {}) => {
    try {
      if (!window.polaris?.pty) {
        throw new Error('PTY API not available');
      }

      // If cwd is not provided, try to use workspace path
      let cwd = options.cwd;

      const res = await window.polaris.pty.start({
        command: '', // Auto-detect in backend
        cwd: cwd,
        env: options.env,
        cols: options.cols || 80,
        rows: options.rows || 24
      });

      if (!res.ok || !res.id) {
        throw new Error(res.error || 'Failed to create terminal session');
      }

      const newSession = {
        id: res.id,
        cols: options.cols || 80,
        rows: options.rows || 24
      };

      sessionRef.current = res.id;
      setSession(newSession);
      return newSession;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error creating terminal';
      setError(message);
      return null;
    }
  }, []);

  const write = useCallback(async (data: string) => {
    if (!sessionRef.current || !window.polaris?.pty) return;
    await window.polaris.pty.write(sessionRef.current, data);
  }, []);

  const resize = useCallback(async (cols: number, rows: number) => {
    if (!sessionRef.current || !window.polaris?.pty) return;
    await window.polaris.pty.resize(sessionRef.current, cols, rows);
    setSession(prev => prev ? { ...prev, cols, rows } : null);
  }, []);

  const close = useCallback(async () => {
    if (!sessionRef.current || !window.polaris?.pty) return;
    await window.polaris.pty.close(sessionRef.current);
    sessionRef.current = null;
    setSession(null);
  }, []);

  useEffect(() => {
    // Cleanup on unmount
    return () => {
      if (sessionRef.current) {
        close();
      }
    };
  }, [close]);

  return {
    session,
    error,
    createSession,
    write,
    resize,
    close
  };
}
