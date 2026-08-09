import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ConnectionStatusIndicator - WebSocket 连接状态指示器
 *
 * 统一的连接状态 UI 组件。Realtime 只允许统一的 Nats-JetStream WebSocket。
 * 提供清晰的视觉反馈，帮助用户理解当前连接状态。
 *
 * Features:
 * - 三色状态指示 (绿=连接, 黄=连接中, 红=断开)
 * - 可配置显示内容
 * - 支持 tooltip 显示详细信息
 * - 响应式设计
 */
import { useState, useCallback, useMemo } from 'react';
import { RefreshCw, AlertTriangle, CheckCircle, XCircle, } from 'lucide-react';
// ============================================================================
// Constants
// ============================================================================
const STATUS_CONFIG = {
    connected: {
        color: 'text-green-500',
        bgColor: 'bg-green-500',
        label: '已连接',
        description: 'WebSocket 连接正常',
        icon: CheckCircle,
    },
    connecting: {
        color: 'text-yellow-500',
        bgColor: 'bg-yellow-500',
        label: '连接中',
        description: '正在建立 WebSocket 连接',
        icon: RefreshCw,
    },
    disconnected: {
        color: 'text-red-500',
        bgColor: 'bg-red-500',
        label: '已断开',
        description: '连接已断开，正在重连',
        icon: XCircle,
    },
};
const SIZE_CLASSES = {
    sm: {
        container: 'w-3 h-3',
        icon: 'w-3 h-3',
        text: 'text-xs',
        gap: 'gap-1',
    },
    md: {
        container: 'w-4 h-4',
        icon: 'w-4 h-4',
        text: 'text-sm',
        gap: 'gap-1.5',
    },
    lg: {
        container: 'w-5 h-5',
        icon: 'w-5 h-5',
        text: 'text-base',
        gap: 'gap-2',
    },
};
// ============================================================================
// Component
// ============================================================================
export function ConnectionStatusIndicator({ connectionState, reconnectAttempt = 0, showLabel = true, showDetails = true, className = '', onClick, onStateChange, size = 'md', pulse = true, }) {
    const [isHovered, setIsHovered] = useState(false);
    const config = STATUS_CONFIG[connectionState];
    const Icon = config.icon;
    const sizeClasses = SIZE_CLASSES[size];
    // Generate tooltip content
    const tooltipContent = useMemo(() => {
        const lines = [config.description];
        if (connectionState === 'connecting' && reconnectAttempt > 0) {
            lines.push(`重试次数: ${reconnectAttempt}`);
        }
        if (connectionState === 'disconnected' && reconnectAttempt > 0) {
            lines.push(`已重试 ${reconnectAttempt} 次`);
        }
        return lines.join('\n');
    }, [config.description, connectionState, reconnectAttempt]);
    const handleClick = useCallback(() => {
        onClick?.();
    }, [onClick]);
    const handleMouseEnter = useCallback(() => {
        setIsHovered(true);
    }, []);
    const handleMouseLeave = useCallback(() => {
        setIsHovered(false);
    }, []);
    // Determine if should pulse
    const shouldPulse = pulse &&
        connectionState === 'connecting';
    return (_jsxs("div", { className: `relative inline-flex items-center ${sizeClasses.gap} ${className}`, onClick: handleClick, onMouseEnter: handleMouseEnter, onMouseLeave: handleMouseLeave, role: onClick ? 'button' : undefined, tabIndex: onClick ? 0 : undefined, title: tooltipContent, "aria-label": `连接状态: ${config.label}`, children: [_jsxs("div", { className: `relative ${sizeClasses.container}`, children: [shouldPulse && (_jsx("span", { className: `absolute inline-flex h-full w-full rounded-full ${config.bgColor} opacity-75 animate-ping`, style: { animationDuration: '2s' } })), _jsx("span", { className: `relative inline-flex ${sizeClasses.container} rounded-full ${config.bgColor}` }), _jsx("span", { className: `absolute inset-0 flex items-center justify-center ${config.color}`, children: _jsx(Icon, { className: sizeClasses.icon, strokeWidth: 2.5 }) })] }), showLabel && (_jsx("span", { className: `${sizeClasses.text} ${config.color} font-medium`, children: config.label })), showDetails && isHovered && (_jsx("div", { className: "absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50 animate-in fade-in duration-200", children: _jsxs("div", { className: "bg-gray-900 text-white text-xs rounded px-3 py-2 shadow-lg whitespace-nowrap", children: [_jsx("div", { className: "font-semibold mb-1", children: config.label }), _jsx("div", { className: "text-gray-300", children: config.description }), connectionState === 'connecting' && reconnectAttempt > 0 && (_jsxs("div", { className: "text-yellow-300 mt-1", children: ["\u91CD\u8BD5 #", reconnectAttempt] })), connectionState === 'disconnected' && reconnectAttempt > 0 && (_jsxs("div", { className: "text-red-300 mt-1", children: ["\u65AD\u5F00\u8FDE\u63A5\uFF0C\u91CD\u8BD5 #", reconnectAttempt] })), _jsx("div", { className: "absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900" })] }) }))] }));
}
export function StatusDot({ connectionState, size = 'md', pulse = true, className = '', }) {
    const config = STATUS_CONFIG[connectionState];
    const shouldPulse = pulse && connectionState === 'connecting';
    const sizeMap = {
        sm: 'w-2 h-2',
        md: 'w-2.5 h-2.5',
        lg: 'w-3 h-3',
    };
    return (_jsxs("span", { className: `relative inline-flex ${sizeMap[size]} ${className}`, children: [shouldPulse && (_jsx("span", { className: `absolute inline-flex h-full w-full rounded-full ${config.bgColor} opacity-75 animate-ping`, style: { animationDuration: '1.5s' } })), _jsx("span", { className: `relative inline-flex ${sizeMap[size]} rounded-full ${config.bgColor}` })] }));
}
export function ConnectionStatusBar({ connectionState, reconnectAttempt = 0, error, onReconnect, }) {
    const config = STATUS_CONFIG[connectionState];
    return (_jsxs("div", { className: `flex items-center justify-between px-3 py-1.5 rounded-md border ${connectionState === 'connected'
            ? 'bg-green-50 border-green-200'
            : connectionState === 'connecting'
                ? 'bg-yellow-50 border-yellow-200'
                : 'bg-red-50 border-red-200'}`, children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(StatusDot, { connectionState: connectionState }), _jsx("span", { className: `text-sm font-medium ${config.color}`, children: config.label }), connectionState === 'connecting' && reconnectAttempt > 0 && (_jsxs("span", { className: "text-xs text-gray-500", children: ["(\u91CD\u8BD5 ", reconnectAttempt, ")"] })), error && (_jsx("span", { className: "text-xs text-red-500 truncate max-w-[200px]", children: error }))] }), connectionState === 'disconnected' && onReconnect && (_jsxs("button", { onClick: onReconnect, className: "flex items-center gap-1 px-2 py-0.5 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors", children: [_jsx(RefreshCw, { className: "w-3 h-3" }), "\u91CD\u8FDE"] }))] }));
}
export function ConnectionStatusPanel({ connectionState, isWebSocketConnected, reconnectAttempt, error, onReconnect, onDisconnect, }) {
    const config = STATUS_CONFIG[connectionState];
    const statusItems = [
        {
            label: '连接模式',
            value: isWebSocketConnected
                ? 'WebSocket'
                : connectionState === 'connecting'
                    ? 'WebSocket 连接中'
                    : '未连接',
            color: isWebSocketConnected
                ? 'text-green-600'
                : connectionState === 'connecting'
                    ? 'text-yellow-600'
                    : 'text-gray-500',
        },
        {
            label: '连接状态',
            value: config.label,
            color: config.color,
        },
        {
            label: '重连次数',
            value: String(reconnectAttempt),
            color: 'text-gray-600',
        },
    ];
    return (_jsxs("div", { className: "bg-white rounded-lg shadow-lg border p-4 min-w-[300px]", children: [_jsxs("div", { className: "flex items-center gap-3 pb-3 border-b", children: [_jsx(StatusDot, { connectionState: connectionState, size: "lg" }), _jsxs("div", { children: [_jsx("div", { className: "font-semibold text-gray-900", children: "\u8FDE\u63A5\u72B6\u6001\u8BE6\u60C5" }), _jsx("div", { className: `text-sm ${config.color}`, children: config.description })] })] }), _jsx("div", { className: "py-3 space-y-2", children: statusItems.map((item) => (_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-sm text-gray-500", children: item.label }), _jsx("span", { className: `text-sm font-medium ${item.color}`, children: item.value })] }, item.label))) }), error && (_jsx("div", { className: "mt-3 p-2 bg-red-50 rounded border border-red-200", children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx(AlertTriangle, { className: "w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" }), _jsx("div", { className: "text-sm text-red-700", children: error })] }) })), _jsxs("div", { className: "flex gap-2 mt-4 pt-3 border-t", children: [_jsxs("button", { onClick: onReconnect, className: "flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-blue-500 hover:bg-blue-600 text-white text-sm rounded transition-colors", children: [_jsx(RefreshCw, { className: "w-4 h-4" }), "\u91CD\u65B0\u8FDE\u63A5"] }), _jsxs("button", { onClick: onDisconnect, className: "flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm rounded transition-colors", children: [_jsx(XCircle, { className: "w-4 h-4" }), "\u65AD\u5F00\u8FDE\u63A5"] })] })] }));
}
// ============================================================================
// Export all components
// ============================================================================
export default ConnectionStatusIndicator;
