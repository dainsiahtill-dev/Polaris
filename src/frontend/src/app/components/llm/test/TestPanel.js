import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, PlayCircle, Eraser } from 'lucide-react';
import { devLogger } from '@/app/utils/devLogger';
import { TerminalOutput } from './TerminalOutput';
import { TestPanelHeader } from './TestPanelHeader';
import { useTestStream } from './hooks/useTestStream';
const EVENT_PREFIX = {
    command: '$',
    stdout: '>',
    stderr: '!',
    response: '<',
    result: '✓',
    error: '✗'
};
const DEFAULT_STATUS_TEXT = {
    idle: '准备就绪',
    running: '测试中',
    success: '成功',
    failed: '失败'
};
const STREAM_VIEW_STATUS_TEXT = {
    idle: '待命',
    running: '流式中',
    success: '已完成',
    failed: '失败'
};
const formatEventLine = (event) => {
    const prefix = EVENT_PREFIX[event.type] || '>';
    const time = new Date(event.timestamp).toLocaleTimeString();
    const details = event.details ? ` ${JSON.stringify(event.details)}` : '';
    return `[${time}] ${prefix} ${event.content}${details}`;
};
const formatEvents = (events) => {
    if (!events.length)
        return '';
    return events.map(formatEventLine).join('\n');
};
const sanitizeFilename = (value) => (value || 'session').replace(/[^A-Za-z0-9_.-]+/g, '_');
export function TestPanel({ provider, events: externalEvents = [], status: externalStatus, onClose, onCancel: externalOnCancel, onTestComplete, role: roleProp = 'connectivity', apiKey, testLevel = 'quick', evaluationMode = 'provider', suites: suitesProp = ['connectivity', 'response'], autoStart = false, runConfig, panelMode = 'stream-runner', title, subtitle, statusText, placeholder, sessionId, streamingEnabled, onStreamingEnabledChange, onClearEvents, embedded = false, }) {
    // 优先使用 runConfig 中的配置
    const suites = runConfig?.suites ?? suitesProp;
    const role = runConfig?.role ?? roleProp;
    const model = runConfig?.model ?? provider.modelId;
    // 内部事件状态 - 用于后端流式输出
    const [events, setEvents] = useState(externalEvents);
    const [internalStatus, setInternalStatus] = useState('idle');
    // Sync external events when they change
    useEffect(() => {
        if (panelMode === 'event-viewer') {
            setEvents(externalEvents);
            return;
        }
        if (externalEvents.length > 0) {
            setEvents(externalEvents);
        }
        else if (externalStatus === 'idle' && internalStatus === 'idle') {
            // Only reset if both are idle (new session)
            setEvents([]);
        }
    }, [externalEvents, externalStatus, internalStatus, panelMode]);
    // 状态优先级：内部流式状态 > 外部控制状态
    // 当流式测试完成时，使用内部状态；否则使用外部状态
    const hasInternalResult = panelMode === 'stream-runner' && (internalStatus === 'success' || internalStatus === 'failed');
    const status = panelMode === 'event-viewer'
        ? (externalStatus ?? internalStatus)
        : (hasInternalResult ? internalStatus : (externalStatus ?? internalStatus));
    const running = status === 'running';
    const statusLabelMap = statusText || (panelMode === 'event-viewer' ? STREAM_VIEW_STATUS_TEXT : DEFAULT_STATUS_TEXT);
    const statusLabel = statusLabelMap[status] || DEFAULT_STATUS_TEXT[status];
    // 流式测试回调 - 使用 useCallback 保持稳定引用
    const handleEvent = useCallback((event) => {
        if (panelMode !== 'stream-runner')
            return;
        devLogger.debug('[TestPanel] handleEvent:', event);
        setEvents((prev) => [...prev, event]);
    }, [panelMode]);
    const handleSuiteStart = useCallback((suite) => {
        if (panelMode !== 'stream-runner')
            return;
        devLogger.debug(`Starting suite: ${suite}`);
    }, [panelMode]);
    const handleSuiteComplete = useCallback((suite, result) => {
        if (panelMode !== 'stream-runner')
            return;
        devLogger.debug(`Suite ${suite}: ${result.ok ? 'PASS' : 'FAIL'}`);
    }, [panelMode]);
    const handleComplete = useCallback(() => {
        if (panelMode !== 'stream-runner')
            return;
        devLogger.debug('[TestPanel] handleComplete called, calling onTestComplete with success: true');
        setInternalStatus('success');
        onTestComplete?.({ success: true, events });
    }, [events, onTestComplete, panelMode]);
    const handleError = useCallback(() => {
        if (panelMode !== 'stream-runner')
            return;
        setInternalStatus('failed');
        onTestComplete?.({ success: false, events });
    }, [events, onTestComplete, panelMode]);
    // 后端流式测试 Hook
    const { startStream, stopStream } = useTestStream({
        onEvent: handleEvent,
        onSuiteStart: handleSuiteStart,
        onSuiteComplete: handleSuiteComplete,
        onComplete: handleComplete,
        onError: handleError,
    });
    // 处理测试启动
    const handleRunTest = useCallback(() => {
        if (panelMode !== 'stream-runner')
            return;
        devLogger.debug('[TestPanel] handleRunTest called');
        // 清空之前的事件
        setEvents([]);
        setInternalStatus('running');
        // 立即添加启动事件，给用户即时反馈
        const now = new Date().toISOString();
        setEvents([
            {
                type: 'stdout',
                timestamp: now,
                content: `正在启动对 ${provider.name} 的测试...`,
            },
            {
                type: 'stdout',
                timestamp: now,
                content: '正在连接到测试服务器...',
            },
        ]);
        // 启动后端流式测试（使用 useTestStream hook 处理所有事件）
        devLogger.debug('[TestPanel] Calling startStream');
        // Extract connection info for HTTP providers (Scheme B support)
        const isHttpConn = provider.conn.kind === 'http';
        const baseUrl = isHttpConn ? provider.conn.baseUrl : undefined;
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
            apiPath: '/v1/chat/completions',
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
    const handlePointerMove = useCallback((event) => {
        if (!dragState.current.active)
            return;
        const deltaX = event.clientX - dragState.current.startX;
        const deltaY = event.clientY - dragState.current.startY;
        setPosition({
            x: dragState.current.originX + deltaX,
            y: dragState.current.originY + deltaY
        });
    }, []);
    const handlePointerUp = useCallback(() => {
        if (!dragState.current.active)
            return;
        dragState.current.active = false;
        setDragging(false);
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
    }, [handlePointerMove]);
    const handlePointerDown = (event) => {
        if (embedded)
            return;
        if (event.button !== 0)
            return;
        const target = event.target;
        if (target.closest('button') || target.closest('input') || target.closest('label'))
            return;
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
    const panelTitle = title || `Testing: ${provider.name}`;
    const panelSubtitleBase = subtitle || `Provider: ${provider.name} · Model: ${provider.modelId || 'default'}`;
    const panelSubtitle = sessionId ? `${panelSubtitleBase} · Session: ${sessionId}` : panelSubtitleBase;
    const terminalPlaceholder = placeholder || (panelMode === 'event-viewer' ? '$ 等待面试流式日志...' : '$ 准备就绪，点击"测试"按钮开始...');
    const handleCopyLogs = async () => {
        if (!logText)
            return;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(logText);
                return;
            }
        }
        catch {
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
        }
        catch {
            // ignore copy failure
        }
    };
    const handleExportLogs = () => {
        if (!logText)
            return;
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
    const headerExtras = panelMode === 'event-viewer' ? (_jsxs("div", { className: "flex items-center gap-2", children: [typeof streamingEnabled === 'boolean' && onStreamingEnabledChange ? (_jsxs("label", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx("input", { type: "checkbox", checked: streamingEnabled, onChange: (event) => onStreamingEnabledChange(event.target.checked), className: "h-3 w-3 rounded border-border bg-[rgba(6,15,28,0.88)] text-accent" }), "\u5B9E\u65F6\u6D41\u5F0F"] })) : null, onClearEvents ? (_jsx("button", { type: "button", onClick: onClearEvents, className: "rounded border border-border p-1.5 text-text-dim hover:border-accent/40 hover:text-text-main", title: "\u6E05\u7A7A\u65E5\u5FD7", children: _jsx(Eraser, { className: "size-3" }) })) : null] })) : null;
    return (_jsxs("div", { className: `soft-panel relative overflow-hidden rounded-lg transition-all ${embedded ? 'flex h-full min-h-0 w-full flex-col' : 'h-fit'} ${collapsed && !embedded ? 'max-w-[240px]' : 'w-full'}`, style: embedded ? undefined : { transform: `translate(${position.x}px, ${position.y}px)` }, children: [_jsx("div", { className: "absolute inset-x-0 top-0 h-px bg-accent/50" }), _jsx("div", { onPointerDown: handlePointerDown, className: `select-none ${embedded ? '' : dragging ? 'cursor-grabbing' : 'cursor-grab'}`, style: { touchAction: 'none' }, children: _jsx(TestPanelHeader, { provider: provider, status: status, onClose: onClose, running: panelMode === 'stream-runner' ? running : false, collapsed: collapsed, onToggleCollapse: () => setCollapsed((prev) => !prev), onCopyLogs: handleCopyLogs, onExportLogs: handleExportLogs, title: panelTitle, subtitle: panelSubtitle, statusText: statusLabelMap, extraActions: headerExtras }) }), !collapsed ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: `${embedded ? 'flex min-h-0 flex-1 flex-col gap-3 p-3' : 'p-4 space-y-3'}`, children: [_jsxs("div", { className: `grid gap-2 text-[10px] text-text-dim ${panelMode === 'event-viewer' ? 'grid-cols-2 2xl:grid-cols-4' : 'grid-cols-1 2xl:grid-cols-3'}`, children: [_jsxs("div", { className: "soft-chip rounded px-2 py-1", children: ["\u72B6\u6001: ", _jsx("span", { className: "text-text-main", children: statusLabel })] }), _jsxs("div", { className: "soft-chip rounded px-2 py-1", children: ["\u63D0\u4F9B\u5546: ", _jsx("span", { className: "text-text-main", children: provider.name })] }), _jsxs("div", { className: "soft-chip rounded px-2 py-1", children: ["\u6A21\u578B: ", _jsx("span", { className: "text-text-main", children: provider.modelId || 'default' })] }), panelMode === 'event-viewer' ? (_jsxs("div", { className: "soft-chip rounded px-2 py-1", children: ["\u4E8B\u4EF6: ", _jsx("span", { className: "text-text-main", children: events.length })] })) : null] }), _jsx(TerminalOutput, { events: events, placeholder: terminalPlaceholder, heightClassName: embedded ? 'min-h-0 flex-1' : panelMode === 'event-viewer' ? 'h-[22rem]' : 'h-80', className: embedded ? 'flex min-h-0 flex-1 flex-col' : undefined })] }), panelMode === 'stream-runner' ? (_jsxs("div", { className: `${embedded ? 'p-3' : 'p-4'} flex shrink-0 items-center gap-2 border-t border-border`, children: [_jsx("button", { type: "button", onClick: () => {
                                    if (running) {
                                        handleCancel();
                                    }
                                    else {
                                        onClose();
                                    }
                                }, disabled: false, className: "rounded border border-border px-4 py-2 text-xs hover:border-status-error/45 hover:text-status-error", children: running ? '取消测试' : '取消' }), _jsxs("button", { type: "button", onClick: handleRunTest, disabled: running, className: "soft-primary-action flex items-center gap-1 rounded px-4 py-2 text-xs disabled:opacity-60", children: [running ? _jsx(Loader2, { className: "size-3 animate-spin" }) : _jsx(PlayCircle, { className: "size-3" }), running ? '测试中...' : '测试'] })] })) : (_jsx("div", { className: `${embedded ? 'p-3' : 'p-4'} shrink-0 border-t border-border text-[10px] text-text-dim`, children: "\u65E5\u5FD7\u7531\u5B9E\u65F6\u4F1A\u8BDD\u9A71\u52A8\uFF0C\u53D1\u9001\u95EE\u9898\u540E\u4F1A\u6301\u7EED\u6D41\u5F0F\u66F4\u65B0\u3002" }))] })) : null] }));
}
