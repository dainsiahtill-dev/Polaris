import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { AlertTriangle, Loader2, Sparkles, TerminalSquare } from 'lucide-react';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle, DrawerDescription, } from '@/app/components/ui/drawer';
import 'xterm/css/xterm.css';
export function PtyDrawer({ open, onOpenChange, roleLabel, providerId, providerConfig, modelValue, onModelChange, onSaveModel, onSaveAndTest, error, showQuickTest = true, quickTestLabel, bootCommand, bootCommandDelayMs = 0, bootCommandLabel, autoCommand, autoCommandOnce = true, autoCommandDelayMs = 0, autoCommandLabel, }) {
    const [terminalNode, setTerminalNode] = useState(null);
    const termRef = useRef(null);
    const fitRef = useRef(null);
    const resizeObserverRef = useRef(null);
    const sessionRef = useRef(null);
    const pendingDataRef = useRef({});
    const openRef = useRef(open);
    const [sessionId, setSessionId] = useState(null);
    const [status, setStatus] = useState('idle');
    const [statusDetail, setStatusDetail] = useState(null);
    const autoCommandSentRef = useRef(false);
    const bootCommandSentRef = useRef(false);
    const autoCommandTimerRef = useRef(null);
    const bootCommandTimerRef = useRef(null);
    useEffect(() => {
        sessionRef.current = sessionId;
    }, [sessionId]);
    openRef.current = open;
    useEffect(() => {
        return () => {
            if (sessionRef.current) {
                window.polaris?.pty?.close(sessionRef.current);
            }
        };
    }, []);
    const providerKey = useMemo(() => {
        if (!providerConfig)
            return '';
        return JSON.stringify({
            command: providerConfig.command,
            args: providerConfig.tui_args || [],
            cwd: providerConfig.working_dir || '',
            env: providerConfig.env || {},
        });
    }, [providerConfig]);
    const handleTerminalRef = useCallback((node) => {
        setTerminalNode(node);
    }, []);
    useEffect(() => {
        if (!open)
            return;
        if (!terminalNode || termRef.current)
            return;
        const term = new Terminal({
            cursorBlink: true,
            fontFamily: '"JetBrains Mono", "Fira Code", "SFMono-Regular", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            fontSize: 12,
            theme: {
                background: '#05060b',
                foreground: '#d8e6ff',
                cursor: '#2bf6ff',
                selectionBackground: 'rgba(43, 246, 255, 0.25)',
                black: '#05060b',
                brightBlack: '#10131b',
                green: '#27f5d0',
                brightGreen: '#49ffd9',
                blue: '#55a7ff',
                brightBlue: '#7cc4ff',
                magenta: '#bf8bff',
                brightMagenta: '#d4a7ff',
                cyan: '#35f3ff',
                brightCyan: '#6ff6ff',
            },
        });
        const fitAddon = new FitAddon();
        term.loadAddon(fitAddon);
        term.open(terminalNode);
        fitAddon.fit();
        term.focus();
        term.writeln('\x1b[38;5;51mPolaris 终端通道已连通。\x1b[0m');
        term.writeln('\x1b[38;5;105m提示：\x1b[0m 在 CLI 输入 /models（或 /model）以查阅模型。');
        term.writeln('');
        term.onData((data) => {
            const current = sessionRef.current;
            if (!current)
                return;
            window.polaris?.pty?.write(current, data);
        });
        term.onResize(({ cols, rows }) => {
            const current = sessionRef.current;
            if (!current)
                return;
            window.polaris?.pty?.resize(current, cols, rows);
        });
        const resizeObserver = new ResizeObserver(() => {
            fitAddon.fit();
            const current = sessionRef.current;
            if (!current)
                return;
            window.polaris?.pty?.resize(current, term.cols, term.rows);
        });
        resizeObserver.observe(terminalNode);
        termRef.current = term;
        fitRef.current = fitAddon;
        resizeObserverRef.current = resizeObserver;
        const current = sessionRef.current;
        if (current) {
            const pending = pendingDataRef.current[current];
            if (pending) {
                term.write(pending);
                delete pendingDataRef.current[current];
            }
        }
        return () => {
            resizeObserver.disconnect();
            term.dispose();
            termRef.current = null;
            fitRef.current = null;
            resizeObserverRef.current = null;
        };
    }, [open, terminalNode]);
    useEffect(() => {
        if (!open) {
            if (sessionRef.current) {
                window.polaris?.pty?.close(sessionRef.current);
            }
            setSessionId(null);
            setStatus('idle');
            setStatusDetail(null);
            autoCommandSentRef.current = false;
            bootCommandSentRef.current = false;
            pendingDataRef.current = {};
            if (autoCommandTimerRef.current != null) {
                window.clearTimeout(autoCommandTimerRef.current);
                autoCommandTimerRef.current = null;
            }
            if (bootCommandTimerRef.current != null) {
                window.clearTimeout(bootCommandTimerRef.current);
                bootCommandTimerRef.current = null;
            }
            return;
        }
        if (!providerConfig?.command) {
            setStatus('error');
            setStatusDetail('缺少 CLI 命令。');
            return;
        }
        if (!window.polaris?.pty?.start) {
            setStatus('error');
            setStatusDetail('PTY 桥接不可用。');
            return;
        }
        let cancelled = false;
        const launch = async () => {
            pendingDataRef.current = {};
            if (sessionRef.current) {
                await window.polaris?.pty?.close(sessionRef.current);
                setSessionId(null);
            }
            setStatus('connecting');
            setStatusDetail(null);
            const term = termRef.current;
            const cols = term?.cols ?? 120;
            const rows = term?.rows ?? 32;
            const result = await window.polaris?.pty?.start({
                command: providerConfig.command || '',
                args: providerConfig.tui_args || [],
                cwd: providerConfig.working_dir || undefined,
                env: providerConfig.env || undefined,
                use_conpty: providerConfig.use_conpty,
                cols,
                rows,
            });
            if (cancelled)
                return;
            if (!result?.ok || !result.id) {
                setStatus('error');
                setStatusDetail(result?.error || '启动 PTY 会话失败。');
                return;
            }
            sessionRef.current = result.id;
            setSessionId(result.id);
            setStatus('online');
            setStatusDetail(null);
            autoCommandSentRef.current = false;
            bootCommandSentRef.current = false;
            const pending = pendingDataRef.current[result.id];
            if (pending && termRef.current) {
                termRef.current.write(pending);
                delete pendingDataRef.current[result.id];
            }
        };
        launch().catch((err) => {
            if (cancelled)
                return;
            setStatus('error');
            setStatusDetail(String(err));
        });
        return () => {
            cancelled = true;
        };
    }, [open, providerKey]);
    const sendCommand = (command) => {
        const current = sessionRef.current;
        if (!current || !window.polaris?.pty?.write)
            return;
        let payload = command;
        if (payload.endsWith('\n') && !payload.endsWith('\r\n')) {
            payload = `${payload.slice(0, -1)}\r`;
        }
        else if (!payload.endsWith('\r') && !payload.endsWith('\n')) {
            payload = `${payload}\r`;
        }
        window.polaris.pty.write(current, payload);
    };
    const sendAutoCommand = (force = false) => {
        if (!autoCommand)
            return;
        if (autoCommandOnce && autoCommandSentRef.current && !force)
            return;
        sendCommand(autoCommand);
        autoCommandSentRef.current = true;
    };
    const handleBootCommand = () => {
        if (!bootCommand)
            return;
        sendCommand(bootCommand);
        bootCommandSentRef.current = true;
    };
    useEffect(() => {
        if (!window.polaris?.pty?.onData)
            return;
        const unsubscribe = window.polaris.pty.onData((payload) => {
            if (!payload || !payload.id)
                return;
            const data = payload.data || '';
            if (!data)
                return;
            const current = sessionRef.current;
            if (payload.id === current) {
                if (termRef.current) {
                    termRef.current.write(data);
                }
                else {
                    const existing = pendingDataRef.current[payload.id] || '';
                    pendingDataRef.current[payload.id] = `${existing}${data}`;
                }
                return;
            }
            if (!openRef.current)
                return;
            const existing = pendingDataRef.current[payload.id] || '';
            pendingDataRef.current[payload.id] = `${existing}${data}`;
        });
        const unsubscribeExit = window.polaris.pty.onExit?.((payload) => {
            if (!payload || payload.id !== sessionRef.current)
                return;
            setStatus('closed');
            setStatusDetail(payload.exitCode != null ? `退出码 ${payload.exitCode}` : '会话已关闭');
        });
        return () => {
            unsubscribe?.();
            unsubscribeExit?.();
        };
    }, []);
    useEffect(() => {
        if (!open || status !== 'online')
            return;
        if (bootCommand && !bootCommandSentRef.current) {
            if (bootCommandTimerRef.current != null) {
                window.clearTimeout(bootCommandTimerRef.current);
            }
            if (bootCommandDelayMs > 0) {
                bootCommandTimerRef.current = window.setTimeout(() => {
                    sendCommand(bootCommand);
                    bootCommandSentRef.current = true;
                    bootCommandTimerRef.current = null;
                }, bootCommandDelayMs);
            }
            else {
                sendCommand(bootCommand);
                bootCommandSentRef.current = true;
            }
        }
        if (autoCommand && (!autoCommandOnce || !autoCommandSentRef.current)) {
            if (autoCommandTimerRef.current != null) {
                window.clearTimeout(autoCommandTimerRef.current);
            }
            if (autoCommandDelayMs > 0) {
                autoCommandTimerRef.current = window.setTimeout(() => {
                    sendAutoCommand();
                    autoCommandTimerRef.current = null;
                }, autoCommandDelayMs);
            }
            else {
                sendAutoCommand();
            }
        }
    }, [
        autoCommand,
        autoCommandDelayMs,
        autoCommandOnce,
        bootCommand,
        bootCommandDelayMs,
        open,
        status,
    ]);
    const handlePaste = async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                onModelChange(text.trim());
            }
        }
        catch {
            // ignore
        }
    };
    return (_jsx(Drawer, { open: open, onOpenChange: onOpenChange, direction: "right", children: _jsxs(DrawerContent, { className: "data-[vaul-drawer-direction=right]:w-[85vw] data-[vaul-drawer-direction=right]:sm:w-[560px] data-[vaul-drawer-direction=right]:sm:max-w-[560px] soft-panel border-l", children: [_jsxs(DrawerHeader, { className: "border-b border-white/10 soft-panel-subtle", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx(TerminalSquare, { className: "size-4" }), _jsx("span", { className: "font-semibold tracking-wide", children: "\u6A21\u578B\u68C0\u9605\u7EC8\u7AEF" })] }), _jsx("span", { className: "text-[10px] uppercase tracking-widest text-text-dim", children: roleLabel })] }), _jsxs(DrawerTitle, { className: "text-sm text-text-main", children: ["\u7EC8\u7AEF\u4F1A\u8BDD - ", providerId] }), _jsxs(DrawerDescription, { className: "text-[11px] text-text-dim", children: ["\u5148\u542F\u52A8 CLI\uFF0C\u901A\u8FC7 ", _jsx("span", { className: "text-accent", children: "/models" }), " \u6216 ", _jsx("span", { className: "text-accent", children: "/model" }), "\u67E5\u9605\u6A21\u578B\uFF0C\u518D\u5C06\u7F16\u53F7\u8D34\u5165\u4E0B\u65B9\u3002"] })] }), _jsxs("div", { className: "flex-1 overflow-hidden p-4 space-y-3", children: [_jsxs("div", { className: "rounded-xl soft-inset", children: [_jsxs("div", { className: "flex items-center justify-between px-3 py-2 border-b border-white/10 text-[10px] uppercase tracking-widest text-text-dim", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Sparkles, { className: "size-3" }), "\u5B9E\u65F6\u7EC8\u7AEF"] }), _jsxs("div", { className: "flex items-center gap-1", children: [status === 'connecting' ? _jsx(Loader2, { className: "size-3 animate-spin" }) : null, _jsx("span", { className: status === 'error' ? 'text-red-300' : status === 'online' ? 'text-emerald-300' : 'text-text-dim', children: { idle: '待命', connecting: '连线中', online: '在线', error: '故障', closed: '已闭' }[status] })] })] }), _jsx("div", { ref: handleTerminalRef, className: "h-[320px] w-full" })] }), status === 'error' ? (_jsxs("div", { className: "flex items-center gap-2 text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded px-3 py-2", children: [_jsx(AlertTriangle, { className: "size-3" }), statusDetail || '无法启动 CLI 会话。'] })) : statusDetail ? (_jsx("div", { className: "text-[10px] text-text-dim", children: statusDetail })) : null, _jsxs("div", { className: "rounded-xl soft-panel-subtle p-3 space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-text-dim uppercase tracking-widest", children: [_jsx("span", { children: "\u5DF2\u9009\u6A21\u578B\u7F16\u53F7" }), _jsxs("div", { className: "flex items-center gap-2", children: [bootCommand ? (_jsx("button", { type: "button", onClick: handleBootCommand, className: "text-accent hover:text-accent/80", children: bootCommandLabel || '启动 Codex' })) : null, autoCommand ? (_jsx("button", { type: "button", onClick: () => sendAutoCommand(true), className: "text-accent hover:text-accent/80", children: autoCommandLabel || '发送 /model' })) : null, _jsx("button", { type: "button", onClick: handlePaste, className: "text-accent hover:text-accent/80", children: "\u7C98\u8D34" })] })] }), _jsx("input", { value: modelValue, onChange: (e) => onModelChange(e.target.value), placeholder: "\u4F8B\uFF1Agpt-4.1-mini", className: "w-full soft-inset text-text-main px-3 py-2 rounded text-sm font-mono focus:outline-none focus:ring-1 focus:ring-accent/40" }), error ? _jsx("div", { className: "text-[10px] text-red-300", children: error }) : null] })] }), _jsxs("div", { className: "p-4 border-t border-white/10 soft-panel-subtle flex items-center justify-between", children: [_jsx("button", { type: "button", onClick: () => onOpenChange(false), className: "px-3 py-1.5 text-[10px] text-text-dim hover:text-text-main border border-white/10 rounded", children: "\u5173\u95ED" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { type: "button", onClick: onSaveModel, className: "px-3 py-1.5 text-[10px] soft-chip text-text-muted hover:text-text-main rounded", children: "\u4FDD\u5B58\u6A21\u578B" }), showQuickTest ? (_jsxs("button", { type: "button", onClick: onSaveAndTest, className: "px-3 py-1.5 text-[10px] soft-raised text-accent rounded flex items-center gap-1", children: [_jsx(Sparkles, { className: "size-3" }), quickTestLabel || '保存并速测'] })) : null] })] })] }) }));
}
