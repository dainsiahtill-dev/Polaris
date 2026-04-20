/**
 * Connectivity Status Utilities
 * 
 * Simplified connectivity status calculation with strict precedence:
 * 1. running (if test is active)
 * 2. Backend connectivity.ok / ready
 * 3. Local test result (if less than X minutes old)
 * 4. Cached status
 * 5. unknown
 */

import type { ConnectivityStatus } from '../state';

export interface ConnectivityResult {
  ok: boolean;
  timestamp: string;
  latencyMs?: number;
  error?: string;
  model?: string;
  sourceRole?: string;
}

// 缓存有效期 (5分钟)
const CACHE_TTL_MS = 5 * 60 * 1000;

/**
 * 计算连接性状态
 * 
 * 优先级顺序:
 * 1. running - 如果测试正在进行中
 * 2. success/failed - 后端状态
 * 3. success/failed - 本地缓存 (如果在有效期内)
 * 4. unknown - 默认状态
 */
export function calculateConnectivityStatus(
  isRunning: boolean,
  backendResult?: { ok?: boolean; ready?: boolean; timestamp?: string } | null,
  cachedResult?: ConnectivityResult | null,
  cacheTtlMs: number = CACHE_TTL_MS
): ConnectivityStatus {
  // 1. 如果测试正在运行，返回 running
  if (isRunning) {
    return 'running';
  }

  // 2. 检查后端状态 (最高优先级)
  if (backendResult) {
    const isReady = backendResult.ok ?? backendResult.ready;
    if (typeof isReady === 'boolean') {
      return isReady ? 'success' : 'failed';
    }
  }

  // 3. 检查本地缓存 (如果在有效期内)
  if (cachedResult) {
    const cacheAge = Date.now() - new Date(cachedResult.timestamp).getTime();
    if (cacheAge <= cacheTtlMs) {
      return cachedResult.ok ? 'success' : 'failed';
    }
  }

  // 4. 默认返回 unknown
  return 'unknown';
}

/**
 * 获取连接性状态的时间戳
 */
export function getConnectivityTimestamp(
  backendResult?: { timestamp?: string } | null,
  cachedResult?: ConnectivityResult | null
): string | undefined {
  // 优先使用后端时间戳
  if (backendResult?.timestamp) {
    return backendResult.timestamp;
  }
  // 其次使用缓存时间戳
  if (cachedResult?.timestamp) {
    return cachedResult.timestamp;
  }
  return undefined;
}

/**
 * 检查缓存是否过期
 */
export function isCacheExpired(
  cachedResult: ConnectivityResult | null | undefined,
  cacheTtlMs: number = CACHE_TTL_MS
): boolean {
  if (!cachedResult) return true;
  const cacheAge = Date.now() - new Date(cachedResult.timestamp).getTime();
  return cacheAge > cacheTtlMs;
}

/**
 * 格式化连接性状态为人类可读的文本
 */
export function formatConnectivityStatus(status: ConnectivityStatus): string {
  switch (status) {
    case 'running':
      return '测试中...';
    case 'success':
      return '连通正常';
    case 'failed':
      return '连通失败';
    case 'unknown':
    default:
      return '未测试';
  }
}

/**
 * 获取连接性状态的颜色类
 */
export function getConnectivityStatusColor(status: ConnectivityStatus): string {
  switch (status) {
    case 'running':
      return 'text-amber-400';
    case 'success':
      return 'text-emerald-400';
    case 'failed':
      return 'text-rose-400';
    case 'unknown':
    default:
      return 'text-gray-400';
  }
}

/**
 * 获取连接性状态的背景色类
 */
export function getConnectivityStatusBgColor(status: ConnectivityStatus): string {
  switch (status) {
    case 'running':
      return 'bg-amber-500/10 border-amber-500/30';
    case 'success':
      return 'bg-emerald-500/10 border-emerald-500/30';
    case 'failed':
      return 'bg-rose-500/10 border-rose-500/30';
    case 'unknown':
    default:
      return 'bg-gray-500/10 border-gray-500/30';
  }
}

/**
 * 获取连接性状态的指示器颜色类
 */
export function getConnectivityStatusDotColor(status: ConnectivityStatus): string {
  switch (status) {
    case 'running':
      return 'bg-amber-400 animate-pulse';
    case 'success':
      return 'bg-emerald-400';
    case 'failed':
      return 'bg-rose-400';
    case 'unknown':
    default:
      return 'bg-gray-400';
  }
}
