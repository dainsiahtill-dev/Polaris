/**
 * ConnectionStatusIndicator - WebSocket/Fallback 连接状态指示器
 *
 * 统一的连接状态 UI 组件，支持 WebSocket 和降级轮询两种模式。
 * 提供清晰的视觉反馈，帮助用户理解当前连接状态。
 *
 * Features:
 * - 三色状态指示 (绿=连接, 黄=降级, 红=断开)
 * - 可配置显示内容
 * - 支持 tooltip 显示详细信息
 * - 响应式设计
 */

import React, { useState, useCallback, useMemo } from 'react';
import {
  Wifi,
  WifiOff,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Radio,
} from 'lucide-react';
import type { ConnectionState } from '../hooks/useWebSocketWithFallback';

// ============================================================================
// Types
// ============================================================================

export interface ConnectionStatusIndicatorProps {
  /** 当前连接状态 */
  connectionState: ConnectionState;
  /** 当前重连尝试次数 */
  reconnectAttempt?: number;
  /** 降级轮询已执行次数 */
  fallbackAttempt?: number;
  /** 是否显示文字标签 */
  showLabel?: boolean;
  /** 是否显示详细信息（tooltip） */
  showDetails?: boolean;
  /** 自定义类名 */
  className?: string;
  /** 点击事件 */
  onClick?: () => void;
  /** 连接状态变更回调 */
  onStateChange?: (state: ConnectionState) => void;
  /** 尺寸大小 */
  size?: 'sm' | 'md' | 'lg';
  /** 是否显示脉冲动画 */
  pulse?: boolean;
}

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
  fallback: {
    color: 'text-amber-500',
    bgColor: 'bg-amber-500',
    label: '降级模式',
    description: 'WebSocket 不可用，使用轮询保持连接',
    icon: Radio,
  },
} as const;

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
} as const;

// ============================================================================
// Component
// ============================================================================

export function ConnectionStatusIndicator({
  connectionState,
  reconnectAttempt = 0,
  fallbackAttempt = 0,
  showLabel = true,
  showDetails = true,
  className = '',
  onClick,
  onStateChange,
  size = 'md',
  pulse = true,
}: ConnectionStatusIndicatorProps): React.ReactElement {
  const [isHovered, setIsHovered] = useState(false);

  const config = STATUS_CONFIG[connectionState];
  const Icon = config.icon;
  const sizeClasses = SIZE_CLASSES[size];

  // Generate tooltip content
  const tooltipContent = useMemo(() => {
    const lines: string[] = [config.description];

    if (connectionState === 'connecting' && reconnectAttempt > 0) {
      lines.push(`重试次数: ${reconnectAttempt}`);
    }

    if (connectionState === 'fallback') {
      lines.push(`轮询次数: ${fallbackAttempt}`);
      lines.push('数据更新可能延迟');
    }

    if (connectionState === 'disconnected' && reconnectAttempt > 0) {
      lines.push(`已重试 ${reconnectAttempt} 次`);
    }

    return lines.join('\n');
  }, [config.description, connectionState, reconnectAttempt, fallbackAttempt]);

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
  const shouldPulse =
    pulse &&
    (connectionState === 'connecting' || connectionState === 'fallback');

  return (
    <div
      className={`relative inline-flex items-center ${sizeClasses.gap} ${className}`}
      onClick={handleClick}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      title={tooltipContent}
      aria-label={`连接状态: ${config.label}`}
    >
      {/* Status Indicator */}
      <div className={`relative ${sizeClasses.container}`}>
        {/* Pulse Ring (for connecting/fallback states) */}
        {shouldPulse && (
          <span
            className={`absolute inline-flex h-full w-full rounded-full ${config.bgColor} opacity-75 animate-ping`}
            style={{ animationDuration: '2s' }}
          />
        )}

        {/* Core Indicator */}
        <span
          className={`relative inline-flex ${sizeClasses.container} rounded-full ${config.bgColor}`}
        />

        {/* Icon Overlay */}
        <span
          className={`absolute inset-0 flex items-center justify-center ${config.color}`}
        >
          <Icon className={sizeClasses.icon} strokeWidth={2.5} />
        </span>
      </div>

      {/* Label */}
      {showLabel && (
        <span className={`${sizeClasses.text} ${config.color} font-medium`}>
          {config.label}
        </span>
      )}

      {/* Tooltip */}
      {showDetails && isHovered && (
        <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50 animate-in fade-in duration-200">
          <div className="bg-gray-900 text-white text-xs rounded px-3 py-2 shadow-lg whitespace-nowrap">
            <div className="font-semibold mb-1">{config.label}</div>
            <div className="text-gray-300">{config.description}</div>

            {connectionState === 'connecting' && reconnectAttempt > 0 && (
              <div className="text-yellow-300 mt-1">
                重试 #{reconnectAttempt}
              </div>
            )}

            {connectionState === 'fallback' && (
              <>
                <div className="text-amber-300 mt-1">
                  轮询 #{fallbackAttempt}
                </div>
                <div className="text-gray-400 text-[10px] mt-1">
                  实时更新可能延迟
                </div>
              </>
            )}

            {connectionState === 'disconnected' && reconnectAttempt > 0 && (
              <div className="text-red-300 mt-1">
                断开连接，重试 #{reconnectAttempt}
              </div>
            )}

            {/* Arrow */}
            <div className="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900" />
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Simplified Status Dot (for compact displays)
// ============================================================================

export interface StatusDotProps {
  connectionState: ConnectionState;
  size?: 'sm' | 'md' | 'lg';
  pulse?: boolean;
  className?: string;
}

export function StatusDot({
  connectionState,
  size = 'md',
  pulse = true,
  className = '',
}: StatusDotProps): React.ReactElement {
  const config = STATUS_CONFIG[connectionState];
  const shouldPulse = pulse && connectionState === 'connecting';

  const sizeMap = {
    sm: 'w-2 h-2',
    md: 'w-2.5 h-2.5',
    lg: 'w-3 h-3',
  };

  return (
    <span className={`relative inline-flex ${sizeMap[size]} ${className}`}>
      {shouldPulse && (
        <span
          className={`absolute inline-flex h-full w-full rounded-full ${config.bgColor} opacity-75 animate-ping`}
          style={{ animationDuration: '1.5s' }}
        />
      )}
      <span
        className={`relative inline-flex ${sizeMap[size]} rounded-full ${config.bgColor}`}
      />
    </span>
  );
}

// ============================================================================
// Status Bar Integration Component
// ============================================================================

export interface ConnectionStatusBarProps {
  connectionState: ConnectionState;
  reconnectAttempt?: number;
  fallbackAttempt?: number;
  error?: string | null;
  onReconnect?: () => void;
}

export function ConnectionStatusBar({
  connectionState,
  reconnectAttempt = 0,
  fallbackAttempt = 0,
  error,
  onReconnect,
}: ConnectionStatusBarProps): React.ReactElement {
  const config = STATUS_CONFIG[connectionState];

  return (
    <div
      className={`flex items-center justify-between px-3 py-1.5 rounded-md border ${
        connectionState === 'connected'
          ? 'bg-green-50 border-green-200'
          : connectionState === 'fallback'
            ? 'bg-amber-50 border-amber-200'
            : 'bg-red-50 border-red-200'
      }`}
    >
      <div className="flex items-center gap-2">
        <StatusDot connectionState={connectionState} />
        <span className={`text-sm font-medium ${config.color}`}>
          {config.label}
        </span>

        {connectionState === 'connecting' && reconnectAttempt > 0 && (
          <span className="text-xs text-gray-500">
            (重试 {reconnectAttempt})
          </span>
        )}

        {connectionState === 'fallback' && (
          <span className="text-xs text-amber-600">
            (轮询 {fallbackAttempt})
          </span>
        )}

        {error && (
          <span className="text-xs text-red-500 truncate max-w-[200px]">
            {error}
          </span>
        )}
      </div>

      {(connectionState === 'disconnected' ||
        connectionState === 'fallback') &&
        onReconnect && (
          <button
            onClick={onReconnect}
            className="flex items-center gap-1 px-2 py-0.5 text-xs bg-gray-100 hover:bg-gray-200 rounded transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            重连
          </button>
        )}
    </div>
  );
}

// ============================================================================
// Full Status Panel (for settings/modal display)
// ============================================================================

export interface ConnectionStatusPanelProps {
  connectionState: ConnectionState;
  isWebSocketConnected: boolean;
  isFallbackActive: boolean;
  reconnectAttempt: number;
  fallbackAttempt: number;
  error: string | null;
  onReconnect: () => void;
  onDisconnect: () => void;
}

export function ConnectionStatusPanel({
  connectionState,
  isWebSocketConnected,
  isFallbackActive,
  reconnectAttempt,
  fallbackAttempt,
  error,
  onReconnect,
  onDisconnect,
}: ConnectionStatusPanelProps): React.ReactElement {
  const config = STATUS_CONFIG[connectionState];

  const statusItems = [
    {
      label: '连接模式',
      value: isWebSocketConnected
        ? 'WebSocket'
        : isFallbackActive
          ? 'HTTP 轮询'
          : '未连接',
      color: isWebSocketConnected
        ? 'text-green-600'
        : isFallbackActive
          ? 'text-amber-600'
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
    {
      label: '轮询次数',
      value: String(fallbackAttempt),
      color: 'text-amber-600',
    },
  ];

  return (
    <div className="bg-white rounded-lg shadow-lg border p-4 min-w-[300px]">
      {/* Header */}
      <div className="flex items-center gap-3 pb-3 border-b">
        <StatusDot connectionState={connectionState} size="lg" />
        <div>
          <div className="font-semibold text-gray-900">
            连接状态详情
          </div>
          <div className={`text-sm ${config.color}`}>
            {config.description}
          </div>
        </div>
      </div>

      {/* Status Items */}
      <div className="py-3 space-y-2">
        {statusItems.map((item) => (
          <div
            key={item.label}
            className="flex items-center justify-between"
          >
            <span className="text-sm text-gray-500">{item.label}</span>
            <span className={`text-sm font-medium ${item.color}`}>
              {item.value}
            </span>
          </div>
        ))}
      </div>

      {/* Error Display */}
      {error && (
        <div className="mt-3 p-2 bg-red-50 rounded border border-red-200">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
            <div className="text-sm text-red-700">{error}</div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 mt-4 pt-3 border-t">
        <button
          onClick={onReconnect}
          className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-blue-500 hover:bg-blue-600 text-white text-sm rounded transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          重新连接
        </button>

        <button
          onClick={onDisconnect}
          className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm rounded transition-colors"
        >
          <XCircle className="w-4 h-4" />
          断开连接
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Export all components
// ============================================================================

export default ConnectionStatusIndicator;
