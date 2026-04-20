import React, { useEffect, useRef, useState } from 'react';
import { TerminalSquare, X, Maximize2, Minimize2, Copy, Eraser, RotateCcw } from 'lucide-react';
import { useTerminal } from './hooks/useTerminal';
import { devLogger } from '@/app/utils/devLogger';
import { toast } from 'sonner';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';

interface TerminalPanelProps {
  isVisible: boolean;
  onClose: () => void;
  workspacePath?: string;
  isMaximized: boolean;
  onToggleMaximize: () => void;
  onResetTasks?: () => void | Promise<void>;
  isResettingTasks?: boolean;
}

export function TerminalPanel({
  isVisible,
  onClose,
  workspacePath,
  isMaximized,
  onToggleMaximize,
  onResetTasks,
  isResettingTasks = false,
}: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const { createSession, write, resize, close } = useTerminal();
  const [isReady, setIsReady] = useState(false);

  // Initialize terminal
  useEffect(() => {
    if (!isVisible || !containerRef.current || terminalRef.current) return;

    const initTerminal = async () => {
      try {
        const term = new Terminal({
          cursorBlink: true,
          fontSize: 14,
          fontFamily: 'Menlo, Monaco, "Courier New", monospace',
          theme: {
            background: '#0f1115',
            foreground: '#e5e7eb',
          },
          allowProposedApi: true,
        });

        const fitAddon = new FitAddon();
        term.loadAddon(fitAddon);

        term.open(containerRef.current!);

        // Initial fit needs to happen after render paint
        setTimeout(() => {
          fitAddon.fit();
        }, 0);

        terminalRef.current = term;
        fitAddonRef.current = fitAddon;

        // Data -> Backend
        term.onData((data) => {
          write(data);
        });

        const session = await createSession({
          cols: term.cols,
          rows: term.rows,
          cwd: workspacePath
        });

        if (session) {
          term.writeln('\x1b[32mTarget environment initialized.\x1b[0m');

          const unsubscribe = window.polaris?.pty?.onData(({ id, data }) => {
            if (id === session.id) {
              term.write(data);
            }
          });

          const handleResize = () => {
            if (terminalRef.current && fitAddonRef.current) {
              fitAddonRef.current.fit();
              const dims = fitAddonRef.current.proposeDimensions();
              if (dims) {
                resize(dims.cols, dims.rows);
              }
            }
          };

          window.addEventListener('resize', handleResize);
          const observer = new ResizeObserver(() => {
            // Debounce slightly or just run
            requestAnimationFrame(handleResize);
          });
          observer.observe(containerRef.current!);

          setIsReady(true);

          // Force a resize calculation after a short delay to ensure everything settled
          setTimeout(handleResize, 100);

          return () => {
            unsubscribe?.();
            window.removeEventListener('resize', handleResize);
            observer.disconnect();
            // Do NOT dispose terminal here if we want to keep it alive on hide?
            // But the effect has dependency [isVisible]. So it will run when visible.
            // If we close, we dispose.
            term.dispose();
            terminalRef.current = null;
            fitAddonRef.current = null;
            close();
          };
        }
      } catch (err) {
        devLogger.error('Failed to init terminal', err);
        toast.error('Failed to initialize terminal');
      }
    };

    initTerminal();

    // We need referencing for cleanup because initTerminal is async
    const currentTermRef = terminalRef;

    return () => {
      // This cleanup runs when component unmounts or isVisible changes to false
      if (currentTermRef.current) {
        currentTermRef.current.dispose();
        currentTermRef.current = null;
        close();
      }
    };

  }, [isVisible]); // Run once on become visible

  // Handle Maximize change specific resize
  useEffect(() => {
    if (isReady && fitAddonRef.current) {
      setTimeout(() => {
        fitAddonRef.current?.fit();
        const dims = fitAddonRef.current?.proposeDimensions();
        if (dims) resize(dims.cols, dims.rows);
      }, 300); // transition delay
    }
  }, [isMaximized, isReady, resize]);


  const handleCopy = () => {
    if (terminalRef.current?.hasSelection()) {
      const selection = terminalRef.current.getSelection();
      navigator.clipboard.writeText(selection);
      terminalRef.current.clearSelection();
      toast.success('Copied to clipboard');
    }
  };

  const handleClear = () => {
    terminalRef.current?.clear();
    terminalRef.current?.writeln('\x1b[2J\x1b[H');
  };

  // ... Render ... (keep existing render structure but use containerRef)


  if (!isVisible) return null;

  return (
    <div className="flex flex-col h-full bg-[#0f1115] border-t border-white/10 relative">
      {/* Terminal Header / Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#181a1f] border-b border-white/5 select-none shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-text-muted flex items-center gap-1.5">
            <TerminalSquare className="size-3.5 text-accent" />
            Terminal
          </span>
          {workspacePath && (
            <span className="text-[10px] text-text-dim px-1.5 py-0.5 rounded bg-white/5 font-mono truncate max-w-[200px]">
              {workspacePath.split(/[\\/]/).pop()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {onResetTasks && (
            <button
              onClick={() => {
                onResetTasks();
              }}
              disabled={isResettingTasks}
              className="px-2 py-1 text-[10px] text-amber-200 bg-amber-500/10 border border-amber-500/30 hover:bg-amber-500/20 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
              title="重置任务（清空历史日志与运行记录）"
            >
              <RotateCcw className={`size-3 ${isResettingTasks ? 'animate-spin' : ''}`} />
              {isResettingTasks ? '重置中' : '重置任务'}
            </button>
          )}
          <button
            onClick={handleCopy}
            className="p-1 text-text-dim hover:text-text-main hover:bg-white/10 rounded transition-colors"
            title="Copy Selection"
          >
            <Copy className="size-3.5" />
          </button>
          <button
            onClick={handleClear}
            className="p-1 text-text-dim hover:text-text-main hover:bg-white/10 rounded transition-colors"
            title="Clear Terminal (Ctrl+L)"
          >
            <Eraser className="size-3.5" />
          </button>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <button
            onClick={onToggleMaximize}
            className={`p-1 hover:bg-white/10 rounded transition-colors ${isMaximized ? 'text-accent' : 'text-text-dim hover:text-text-main'}`}
            title={isMaximized ? "Restore Size" : "Maximize Panel"}
          >
            {isMaximized ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
          </button>
          <button
            onClick={onClose}
            className="p-1 text-text-dim hover:text-status-error hover:bg-status-error/10 rounded transition-colors"
            title="Close Terminal"
          >
            <X className="size-3.5" />
          </button>
        </div>
      </div>

      {/* Terminal Container */}
      <div className="flex-1 min-h-0 bg-[#0f1115] relative p-1 pl-2">
        <div ref={containerRef} className="size-full" />
      </div>
    </div>
  );
}
