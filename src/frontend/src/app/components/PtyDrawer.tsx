import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { AlertTriangle, Loader2, Sparkles, TerminalSquare } from 'lucide-react';
import {
  Drawer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
  DrawerDescription,
} from '@/app/components/ui/drawer';
import 'xterm/css/xterm.css';

interface PtyProviderConfig {
  id: string;
  command?: string;
  working_dir?: string;
  env?: Record<string, string>;
  tui_args?: string[];
  use_conpty?: boolean;
}

interface PtyDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roleLabel: string;
  providerId: string;
  providerConfig?: PtyProviderConfig | null;
  modelValue: string;
  onModelChange: (value: string) => void;
  onSaveModel: () => Promise<void> | void;
  onSaveAndTest: () => Promise<void> | void;
  error?: string | null;
  showQuickTest?: boolean;
  quickTestLabel?: string;
  bootCommand?: string;
  bootCommandDelayMs?: number;
  bootCommandLabel?: string;
  autoCommand?: string;
  autoCommandOnce?: boolean;
  autoCommandDelayMs?: number;
  autoCommandLabel?: string;
}

export function PtyDrawer({
  open,
  onOpenChange,
  roleLabel,
  providerId,
  providerConfig,
  modelValue,
  onModelChange,
  onSaveModel,
  onSaveAndTest,
  error,
  showQuickTest = true,
  quickTestLabel,
  bootCommand,
  bootCommandDelayMs = 0,
  bootCommandLabel,
  autoCommand,
  autoCommandOnce = true,
  autoCommandDelayMs = 0,
  autoCommandLabel,
}: PtyDrawerProps) {
  const [terminalNode, setTerminalNode] = useState<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const sessionRef = useRef<string | null>(null);
  const pendingDataRef = useRef<Record<string, string>>({});
  const openRef = useRef(open);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'connecting' | 'online' | 'error' | 'closed'>('idle');
  const [statusDetail, setStatusDetail] = useState<string | null>(null);
  const autoCommandSentRef = useRef(false);
  const bootCommandSentRef = useRef(false);
  const autoCommandTimerRef = useRef<number | null>(null);
  const bootCommandTimerRef = useRef<number | null>(null);

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
    if (!providerConfig) return '';
    return JSON.stringify({
      command: providerConfig.command,
      args: providerConfig.tui_args || [],
      cwd: providerConfig.working_dir || '',
      env: providerConfig.env || {},
    });
  }, [providerConfig]);

  const handleTerminalRef = useCallback((node: HTMLDivElement | null) => {
    setTerminalNode(node);
  }, []);

  useEffect(() => {
    if (!open) return;
    if (!terminalNode || termRef.current) return;

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
      if (!current) return;
      window.polaris?.pty?.write(current, data);
    });

    term.onResize(({ cols, rows }) => {
      const current = sessionRef.current;
      if (!current) return;
      window.polaris?.pty?.resize(current, cols, rows);
    });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      const current = sessionRef.current;
      if (!current) return;
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
      if (cancelled) return;
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
      if (cancelled) return;
      setStatus('error');
      setStatusDetail(String(err));
    });

    return () => {
      cancelled = true;
    };
  }, [open, providerKey]);

  const sendCommand = (command: string) => {
    const current = sessionRef.current;
    if (!current || !window.polaris?.pty?.write) return;
    let payload = command;
    if (payload.endsWith('\n') && !payload.endsWith('\r\n')) {
      payload = `${payload.slice(0, -1)}\r`;
    } else if (!payload.endsWith('\r') && !payload.endsWith('\n')) {
      payload = `${payload}\r`;
    }
    window.polaris.pty.write(current, payload);
  };

  const sendAutoCommand = (force = false) => {
    if (!autoCommand) return;
    if (autoCommandOnce && autoCommandSentRef.current && !force) return;
    sendCommand(autoCommand);
    autoCommandSentRef.current = true;
  };

  const handleBootCommand = () => {
    if (!bootCommand) return;
    sendCommand(bootCommand);
    bootCommandSentRef.current = true;
  };

  useEffect(() => {
    if (!window.polaris?.pty?.onData) return;
    const unsubscribe = window.polaris.pty.onData((payload) => {
      if (!payload || !payload.id) return;
      const data = payload.data || '';
      if (!data) return;
      const current = sessionRef.current;
      if (payload.id === current) {
        if (termRef.current) {
          termRef.current.write(data);
        } else {
          const existing = pendingDataRef.current[payload.id] || '';
          pendingDataRef.current[payload.id] = `${existing}${data}`;
        }
        return;
      }
      if (!openRef.current) return;
      const existing = pendingDataRef.current[payload.id] || '';
      pendingDataRef.current[payload.id] = `${existing}${data}`;
    });
    const unsubscribeExit = window.polaris.pty.onExit?.((payload) => {
      if (!payload || payload.id !== sessionRef.current) return;
      setStatus('closed');
      setStatusDetail(payload.exitCode != null ? `退出码 ${payload.exitCode}` : '会话已关闭');
    });
    return () => {
      unsubscribe?.();
      unsubscribeExit?.();
    };
  }, []);

  useEffect(() => {
    if (!open || status !== 'online') return;
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
      } else {
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
      } else {
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
    } catch {
      // ignore
    }
  };

  return (
    <Drawer open={open} onOpenChange={onOpenChange} direction="right">
      <DrawerContent className="data-[vaul-drawer-direction=right]:w-[85vw] data-[vaul-drawer-direction=right]:sm:w-[560px] data-[vaul-drawer-direction=right]:sm:max-w-[560px] bg-[#05060b] border-l border-cyan-400/20 shadow-[0_0_40px_rgba(45,212,191,0.12)]">
        <DrawerHeader className="border-b border-white/10 bg-gradient-to-r from-cyan-500/10 via-blue-500/5 to-purple-500/10">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs text-cyan-200">
              <TerminalSquare className="size-4" />
              <span className="font-semibold tracking-wide">模型检阅终端</span>
            </div>
            <span className="text-[10px] uppercase tracking-widest text-cyan-400/70">{roleLabel}</span>
          </div>
          <DrawerTitle className="text-sm text-text-main">终端会话 - {providerId}</DrawerTitle>
          <DrawerDescription className="text-[11px] text-text-dim">
            先启动 CLI，通过 <span className="text-cyan-200">/models</span> 或 <span className="text-cyan-200">/model</span>
            查阅模型，再将编号贴入下方。
          </DrawerDescription>
        </DrawerHeader>

        <div className="flex-1 overflow-hidden p-4 space-y-3">
          <div className="rounded-xl border border-cyan-400/20 bg-gradient-to-br from-cyan-500/5 via-black/60 to-purple-500/5 shadow-[0_0_24px_rgba(56,189,248,0.15)]">
            <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 text-[10px] uppercase tracking-widest text-cyan-200">
              <div className="flex items-center gap-2">
                <Sparkles className="size-3" />
                实时终端
              </div>
              <div className="flex items-center gap-1">
                {status === 'connecting' ? <Loader2 className="size-3 animate-spin" /> : null}
                <span className={status === 'error' ? 'text-red-300' : status === 'online' ? 'text-emerald-300' : 'text-cyan-200/70'}>
                  {{ idle: '待命', connecting: '连线中', online: '在线', error: '故障', closed: '已闭' }[status]}
                </span>
              </div>
            </div>
            <div className="relative">
              <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(circle_at_20%_10%,rgba(34,211,238,0.12),transparent_60%),radial-gradient(circle_at_80%_20%,rgba(168,85,247,0.12),transparent_60%)]" />
              <div ref={handleTerminalRef} className="h-[320px] w-full relative z-10" />
            </div>
          </div>

          {status === 'error' ? (
            <div className="flex items-center gap-2 text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded px-3 py-2">
              <AlertTriangle className="size-3" />
              {statusDetail || '无法启动 CLI 会话。'}
            </div>
          ) : statusDetail ? (
            <div className="text-[10px] text-text-dim">{statusDetail}</div>
          ) : null}

          <div className="rounded-xl border border-white/10 bg-black/40 p-3 space-y-2">
            <div className="flex items-center justify-between text-[10px] text-text-dim uppercase tracking-widest">
              <span>已选模型编号</span>
              <div className="flex items-center gap-2">
                {bootCommand ? (
                  <button type="button" onClick={handleBootCommand} className="text-cyan-200 hover:text-cyan-100">
                    {bootCommandLabel || '启动 Codex'}
                  </button>
                ) : null}
                {autoCommand ? (
                  <button type="button" onClick={() => sendAutoCommand(true)} className="text-cyan-200 hover:text-cyan-100">
                    {autoCommandLabel || '发送 /model'}
                  </button>
                ) : null}
                <button type="button" onClick={handlePaste} className="text-cyan-200 hover:text-cyan-100">
                  粘贴
                </button>
              </div>
            </div>
            <input
              value={modelValue}
              onChange={(e) => onModelChange(e.target.value)}
              placeholder="例：gpt-4.1-mini"
              className="w-full bg-black/40 text-text-main px-3 py-2 rounded border border-cyan-400/20 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-cyan-400/40"
            />
            {error ? <div className="text-[10px] text-red-300">{error}</div> : null}
          </div>
        </div>

        <div className="p-4 border-t border-white/10 bg-black/30 flex items-center justify-between">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="px-3 py-1.5 text-[10px] text-text-dim hover:text-text-main border border-white/10 rounded"
          >
            关闭
          </button>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onSaveModel}
              className="px-3 py-1.5 text-[10px] text-cyan-100 bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-400/30 rounded"
            >
              保存模型
            </button>
            {showQuickTest ? (
              <button
                type="button"
                onClick={onSaveAndTest}
                className="px-3 py-1.5 text-[10px] text-black bg-cyan-400 hover:bg-cyan-300 rounded flex items-center gap-1"
              >
                <Sparkles className="size-3" />
                {quickTestLabel || '保存并速测'}
              </button>
            ) : null}
          </div>
        </div>
      </DrawerContent>
    </Drawer>
  );
}
