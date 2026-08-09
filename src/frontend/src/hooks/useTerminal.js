import { useState, useCallback, useRef, useEffect } from 'react';
export function useTerminal() {
    const [session, setSession] = useState(null);
    const [error, setError] = useState(null);
    const sessionRef = useRef(null);
    const createSession = useCallback(async (options = {}) => {
        try {
            if (!window.polaris?.pty) {
                throw new Error('PTY API not available');
            }
            const res = await window.polaris.pty.start({
                command: '',
                cwd: options.cwd,
                env: options.env,
                cols: options.cols || 80,
                rows: options.rows || 24,
            });
            if (!res.ok || !res.id) {
                throw new Error(res.error || 'Failed to create terminal session');
            }
            const newSession = {
                id: res.id,
                cols: options.cols || 80,
                rows: options.rows || 24,
            };
            sessionRef.current = res.id;
            setSession(newSession);
            return newSession;
        }
        catch (err) {
            setError(err instanceof Error ? err.message : 'Unknown error creating terminal');
            return null;
        }
    }, []);
    const write = useCallback(async (data) => {
        if (!sessionRef.current || !window.polaris?.pty)
            return;
        await window.polaris.pty.write(sessionRef.current, data);
    }, []);
    const resize = useCallback(async (cols, rows) => {
        if (!sessionRef.current || !window.polaris?.pty)
            return;
        await window.polaris.pty.resize(sessionRef.current, cols, rows);
        setSession(prev => prev ? { ...prev, cols, rows } : null);
    }, []);
    const close = useCallback(async () => {
        if (!sessionRef.current || !window.polaris?.pty)
            return;
        await window.polaris.pty.close(sessionRef.current);
        sessionRef.current = null;
        setSession(null);
    }, []);
    useEffect(() => {
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
        close,
    };
}
