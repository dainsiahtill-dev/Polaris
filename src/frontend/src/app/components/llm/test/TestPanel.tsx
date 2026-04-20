import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Loader2, PlayCircle, Eraser } from 'lucide-react';
import type { SimpleProvider } from '../types';
import { devLogger } from '@/app/utils/devLogger';
import type { TestEvent, TestEventType } from './types';
import { TerminalOutput } from './TerminalOutput';
import { TestPanelHeader } from './TestPanelHeader';
import { useTestStream } from './hooks/useTestStream';

type PanelStatus = 'idle' | 'running' | 'success' | 'failed';
type TestPanelMode = 'stream-runner' | 'event-viewer';

interface TestPanelProps {
  provider: SimpleProvider;
  events?: TestEvent[]; // 静态事件数组（可选，用于兼容性）
  status?: PanelStatus;
  onClose: () => void;
  onCancel?: () => void;
  // 测试完成回调（包含结果）
  onTestComplete?: (result: { success: boolean; events: TestEvent[] }) => void;
  // 用于 SSE 流式测试的配置
  role?: string;
  apiKey?: string | null;
  testLevel?: string;
  evaluationMode?: string;
  suites?: string[];
  // 是否自动开始测试（默认 false，需要用户手动点击）
  autoStart?: boolean;
  // 测试运行配置（优先于上述单独配置）
  runConfig?: {
    suites?: string[];
    role?: string;
    model?: string;
  };
  panelMode?: TestPanelMode;
  title?: string;
  subtitle?: string;
  statusText?: Partial<Record<PanelStatus, string>>;
  placeholder?: string;
  sessionId?: string | null;
  streamingEnabled?: boolean;
  onStreamingEnabledChange?: (enabled: boolean) => void;
  onClearEvents?: () => void;
}

const EVENT_PREFIX: Record<TestEventType, string> = {
  command: '$',
  stdout: '>',
  stderr: '!',
  response: '<',
  result: '✓',
  error: '✗'
};

const DEFAULT_STATUS_TEXT: Record<PanelStatus, string> = {
  idle: '准备就绪',
  running: '测试中',
  success: '成功',
  failed: '失败'
};

const STREAM_VIEW_STATUS_TEXT: Record<PanelStatus, string> = {
  idle: '待命',
  running: '流式中',
  success: '已完成',
  failed: '失败'
};

const formatEventLine = (event: TestEvent) => {
  const prefix = EVENT_PREFIX[event.type] || '>';
  const time = new Date(event.timestamp).toLocaleTimeString();
  const details = event.details ? ` ${JSON.stringify(event.details)}` : '';
  return `[${time}] ${prefix} ${event.content}${details}`;
};

const formatEvents = (events: TestEvent[]) => {
  if (!events.length) return '';
  return events.map(formatEventLine).join('\n');
};

const sanitizeFilename = (value: string) => (value || 'session').replace(/[^A-Za-z0-9_.-]+/g, '_');

export function TestPanel({
  provider,
  events: externalEvents = [],
  status: externalStatus,
  onClose,
  onCancel: externalOnCancel,
  onTestComplete,
  role: roleProp = 'connectivity',
  apiKey,
  testLevel = 'quick',
  evaluationMode = 'provider',
  suites: suitesProp = ['connectivity', 'response'],
  autoStart = false,
  runConfig,
  panelMode = 'stream-runner',
  title,
  subtitle,
  statusText,
  placeholder,
  sessionId,
  streamingEnabled,
  onStreamingEnabledChange,
  onClearEvents,
}: TestPanelProps) {
  // 优先使用 runConfig 中的配置
  const suites = runConfig?.suites ?? suitesProp;
  const role = runConfig?.role ?? roleProp;
  const model = runConfig?.model ?? provider.modelId;
  // 内部事件状态 - 用于 SSE 流式输出
  const [events, setEvents] = useState<TestEvent[]>(externalEvents);
  const [internalStatus, setInternalStatus] = useState<PanelStatus>('idle');

  // Sync external events when they change
  useEffect(() => {
    if (panelMode === 'event-viewer') {
      setEvents(externalEvents);
      return;
    }
    if (externalEvents.length > 0) {
      setEvents(externalEvents);
    } else if (externalStatus === 'idle' && internalStatus === 'idle') {
      // Only reset if both are idle (new session)
      setEvents([]);
    }
  }, [externalEvents, externalStatus, internalStatus, panelMode]);
  
  // 状态优先级：内部流式状态 > 外部控制状态
  // 当流式测试完成时，使用内部状态；否则使用外部状态
  const hasInternalResult =
    panelMode === 'stream-runner' && (internalStatus === 'success' || internalStatus === 'failed');
  const status: PanelStatus =
    panelMode === 'event-viewer'
      ? (externalStatus ?? internalStatus)
      : (hasInternalResult ? internalStatus : (externalStatus ?? internalStatus));
  const running = status === 'running';
  const statusLabelMap = statusText || (panelMode === 'event-viewer' ? STREAM_VIEW_STATUS_TEXT : DEFAULT_STATUS_TEXT);
  const statusLabel = statusLabelMap[status] || DEFAULT_STATUS_TEXT[status];
  
  // SSE 流式测试回调 - 使用 useCallback 保持稳定引用
  const handleEvent = useCallback((event: TestEvent) => {
    if (panelMode !== 'stream-runner') return;
    devLogger.debug('[TestPanel] handleEvent:', event);
    setEvents((prev) => [...prev, event]);
  }, [panelMode]);
  
  const handleSuiteStart = useCallback((suite: string) => {
    if (panelMode !== 'stream-runner') return;
    devLogger.debug(`Starting suite: ${suite}`);
  }, [panelMode]);
  
  const handleSuiteComplete = useCallback((suite: string, result: { ok: boolean }) => {
    if (panelMode !== 'stream-runner') return;
    devLogger.debug(`Suite ${suite}: ${result.ok ? 'PASS' : 'FAIL'}`);
  }, [panelMode]);
  
  const handleComplete = useCallback(() => {
    if (panelMode !== 'stream-runner') return;
    devLogger.debug('[TestPanel] handleComplete called, calling onTestComplete with success: true');
    setInternalStatus('success');
    onTestComplete?.({ success: true, events });
  }, [events, onTestComplete, panelMode]);
  
  const handleError = useCallback(() => {
    if (panelMode !== 'stream-runner') return;
    setInternalStatus('failed');
    onTestComplete?.({ success: false, events });
  }, [events, onTestComplete, panelMode]);
  
  // SSE 流式测试 Hook
  const { startStream, stopStream } = useTestStream({
    onEvent: handleEvent,
    onSuiteStart: handleSuiteStart,
    onSuiteComplete: handleSuiteComplete,
    onComplete: handleComplete,
    onError: handleError,
  });

  // 处理测试启动
  const handleRunTest = useCallback(() => {
    if (panelMode !== 'stream-runner') return;
    devLogger.debug('[TestPanel] handleRunTest called');
    // 清空之前的事件
    setEvents([]);
    setInternalStatus('running');
    
    // 🚀 立即添加启动事件，给用户即时反馈
    const now = new Date().toISOString();
    setEvents([
      {
        type: 'stdout',
        timestamp: now,
        content: `🚀 正在启动对 ${provider.name} 的测试...`,
      },
      {
        type: 'stdout',
        timestamp: now,
        content: `📡 正在连接到测试服务器...`,
      },
    ]);
    
    // 启动 SSE 流式测试（使用 useTestStream hook 处理所有事件）
    devLogger.debug('[TestPanel] Calling startStream');
    
    // Extract connection info for HTTP providers (Scheme B support)
    const isHttpConn = provider.conn.kind === 'http';
    const baseUrl = isHttpConn ? (provider.conn as { kind: 'http'; baseUrl: string }).baseUrl : undefined;
    
    // 使用传入的 suites 配置（支持深度面试的多 suite 测试）
    const testSuites = suites?.length ? suites : ['connectivity'];
    
    devLogger.debug('[TestPanel] Using test config:', { role, model, suites: testSuites });
    
    startStream({
      role,
      providerId: provider.id,
      model: model || 'default',
      suites: testSuites,
      testLevel,
      evaluationMode,
      apiKey,
      // Scheme B: Pass direct config for connectivity-only tests
      providerType: provider.kind,
      baseUrl,
      apiPath: '/v1/chat/completions', // Default for most OpenAI-compatible APIs
      timeout: 30,
    });
    
    // 注意：不调用 externalOnRunTest，避免双重请求
    // useTestStream 会通过 onEvent 回调更新 events 状态
  }, [provider, role, model, suites, testLevel, evaluationMode, apiKey, startStream, panelMode]);

  // autoStart 控制是否自动开始测试
  // 当 autoStart 从 false 变为 true 时，自动触发测试
  useEffect(() => {
    if (panelMode === 'stream-runner' && autoStart && internalStatus === 'idle') {
      devLogger.debug('[TestPanel] autoStart triggered, starting test...');
      handleRunTest();
    }
  }, [autoStart, internalStatus, handleRunTest, panelMode]);

  // 处理取消
  const handleCancel = useCallback(() => {
    if (panelMode === 'stream-runner') {
      stopStream();
      setInternalStatus('idle');
    }
    externalOnCancel?.();
  }, [stopStream, externalOnCancel, panelMode]);
  
  const [collapsed, setCollapsed] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragState = useRef({ active: false, startX: 0, startY: 0, originX: 0, originY: 0 });

  const handlePointerMove = useCallback((event: PointerEvent) => {
    if (!dragState.current.active) return;
    const deltaX = event.clientX - dragState.current.startX;
    const deltaY = event.clientY - dragState.current.startY;
    setPosition({
      x: dragState.current.originX + deltaX,
      y: dragState.current.originY + deltaY
    });
  }, []);

  const handlePointerUp = useCallback(() => {
    if (!dragState.current.active) return;
    dragState.current.active = false;
    setDragging(false);
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('pointerup', handlePointerUp);
  }, [handlePointerMove]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest('button') || target.closest('input') || target.closest('label')) return;
    event.preventDefault();
    dragState.current = {
      active: true,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y
    };
    setDragging(true);
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
  };

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [handlePointerMove, handlePointerUp]);

  const logText = useMemo(() => formatEvents(events), [events]);

  const panelTitle = title || `🖥️ Testing: ${provider.name}`;
  const panelSubtitleBase = subtitle || `Provider: ${provider.name} · Model: ${provider.modelId || 'default'}`;
  const panelSubtitle = sessionId ? `${panelSubtitleBase} · Session: ${sessionId}` : panelSubtitleBase;
  const terminalPlaceholder =
    placeholder || (panelMode === 'event-viewer' ? '$ 等待面试流式日志...' : '$ 准备就绪，点击"测试"按钮开始...');

  const handleCopyLogs = async () => {
    if (!logText) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(logText);
        return;
      }
    } catch {
      // fallback below
    }
    try {
      const textarea = document.createElement('textarea');
      textarea.value = logText;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    } catch {
      // ignore copy failure
    }
  };

  const handleExportLogs = () => {
    if (!logText) return;
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `${sanitizeFilename(provider.name)}-${stamp}.log`;
    const blob = new Blob([logText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  const headerExtras =
    panelMode === 'event-viewer' ? (
      <div className="flex items-center gap-2">
        {typeof streamingEnabled === 'boolean' && onStreamingEnabledChange ? (
          <label className="flex items-center gap-1 text-[10px] text-text-dim">
            <input
              type="checkbox"
              checked={streamingEnabled}
              onChange={(event) => onStreamingEnabledChange(event.target.checked)}
              className="h-3 w-3 rounded border-white/20 bg-black/40"
            />
            实时流式
          </label>
        ) : null}
        {onClearEvents ? (
          <button
            type="button"
            onClick={onClearEvents}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 text-text-dim"
            title="清空日志"
          >
            <Eraser className="size-3" />
          </button>
        ) : null}
      </div>
    ) : null;

  return (
    <div
      className={`relative bg-black/30 bg-gradient-to-br from-cyan-500/10 via-purple-500/10 to-pink-500/10 rounded-xl border border-cyan-400/30 shadow-[0_0_20px_rgba(34,211,238,0.18),0_0_40px_rgba(168,85,247,0.12)] backdrop-blur-xl h-fit overflow-hidden transition-all ${
        collapsed ? 'max-w-[240px]' : 'w-full'
      }`}
      style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
    >
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-cyan-400/60 via-fuchsia-400/50 to-pink-400/60" />
      <div
        onPointerDown={handlePointerDown}
        className={`select-none ${dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
        style={{ touchAction: 'none' }}
      >
        <TestPanelHeader
          provider={provider}
          status={status}
          onClose={onClose}
          running={panelMode === 'stream-runner' ? running : false}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((prev) => !prev)}
          onCopyLogs={handleCopyLogs}
          onExportLogs={handleExportLogs}
          title={panelTitle}
          subtitle={panelSubtitle}
          statusText={statusLabelMap}
          extraActions={headerExtras}
        />
      </div>

      {!collapsed ? (
        <>
          <div className="p-4 space-y-3">
            <div className={`grid gap-3 text-[10px] text-text-dim ${panelMode === 'event-viewer' ? 'grid-cols-4' : 'grid-cols-3'}`}>
              <div className="rounded border border-white/10 bg-black/20 px-2 py-1">
                状态: <span className="text-text-main">{statusLabel}</span>
              </div>
              <div className="rounded border border-white/10 bg-black/20 px-2 py-1">
                提供商: <span className="text-text-main">{provider.name}</span>
              </div>
              <div className="rounded border border-white/10 bg-black/20 px-2 py-1">
                模型: <span className="text-text-main">{provider.modelId || 'default'}</span>
              </div>
              {panelMode === 'event-viewer' ? (
                <div className="rounded border border-white/10 bg-black/20 px-2 py-1">
                  事件: <span className="text-text-main">{events.length}</span>
                </div>
              ) : null}
            </div>

            <TerminalOutput
              events={events}
              placeholder={terminalPlaceholder}
              heightClassName={panelMode === 'event-viewer' ? 'h-[22rem]' : 'h-80'}
            />
          </div>

          {panelMode === 'stream-runner' ? (
            <div className="p-4 border-t border-white/10 flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  if (running) {
                    handleCancel();
                  } else {
                    onClose();
                  }
                }}
                disabled={false}
                className="px-4 py-2 text-xs border border-white/10 rounded hover:border-red-400/40"
              >
                {running ? '取消测试' : '取消'}
              </button>
              <button
                type="button"
                onClick={handleRunTest}
                disabled={running}
                className="px-4 py-2 text-xs bg-emerald-500/80 hover:bg-emerald-500 text-white rounded disabled:opacity-60 flex items-center gap-1"
              >
                {running ? <Loader2 className="size-3 animate-spin" /> : <PlayCircle className="size-3" />}
                {running ? '测试中...' : '测试'}
              </button>
            </div>
          ) : (
            <div className="p-4 border-t border-white/10 text-[10px] text-text-dim">
              日志由实时会话驱动，发送问题后会持续流式更新。
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
