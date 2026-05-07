/**
 * 开发日志工具 - Polaris
 * 统一管理前端日志输出，减少生产环境噪音
 *
 * 用法:
 *   import { devLogger, createLogger } from '@/app/utils/devLogger';
 *   const logger = createLogger('MyComponent');
 *   logger.debug('message', data);
 *   logger.error('message', error);
 *
 * 生产控制:
 *   localStorage.setItem('polaris:debug:disable', 'true')  // 禁用日志
 */

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

interface LoggerInstance {
  debug: (message: string, ...args: unknown[]) => void;
  info: (message: string, ...args: unknown[]) => void;
  warn: (message: string, ...args: unknown[]) => void;
  error: (message: string, ...args: unknown[]) => void;
  log: (message: string, ...args: unknown[]) => void;
}

const isDev = import.meta.env.DEV;
const isTest = import.meta.env.MODE === 'test';
const forceDebug = import.meta.env.VITE_POLARIS_FORCE_DEBUG === '1';

const isDebugDisabled = (): boolean => {
  try {
    return localStorage.getItem('polaris:debug:disable') === 'true';
  } catch {
    return false;
  }
};

const shouldLog = (level: LogLevel = 'debug'): boolean => {
  if (level === 'error') return true; // error 始终输出
  if (isTest) return false;
  return (forceDebug && !isDebugDisabled()) || isDev;
};

/**
 * 核心日志方法
 */
export const devLogger = {
  debug: (message: string, ...args: unknown[]) => {
    if (shouldLog('debug')) console.debug(`[DEBUG] ${message}`, ...args);
  },

  info: (message: string, ...args: unknown[]) => {
    if (shouldLog('info')) console.info(`[INFO] ${message}`, ...args);
  },

  warn: (message: string, ...args: unknown[]) => {
    if (shouldLog('warn')) console.warn(`[WARN] ${message}`, ...args);
    // TODO: 生产环境可选上报到监控服务
  },

  error: (message: string, ...args: unknown[]) => {
    console.error(`[ERROR] ${message}`, ...args);
    // TODO: 可选上报到错误监控服务
  },

  log: (message: string, ...args: unknown[]) => {
    if (shouldLog('debug')) console.log(`[LOG] ${message}`, ...args);
  },
};

/**
 * 向后兼容的导出
 */
export const devLog = {
  log: (...args: unknown[]) => {
    if (shouldLog('debug')) console.log(...args);
  },
  debug: (...args: unknown[]) => {
    if (shouldLog('debug')) console.debug(...args);
  },
  warn: (...args: unknown[]) => {
    if (shouldLog('warn')) console.warn(...args);
  },
  error: (...args: unknown[]) => {
    console.error(...args);
  },
};

/**
 * 创建带前缀的 logger 实例
 * @param prefix 日志前缀，如组件名
 * @returns Logger instance
 */
export const createLogger = (prefix: string): LoggerInstance => ({
  debug: (msg: string, ...args: unknown[]) =>
    devLogger.debug(`[${prefix}] ${msg}`, ...args),
  info: (msg: string, ...args: unknown[]) =>
    devLogger.info(`[${prefix}] ${msg}`, ...args),
  warn: (msg: string, ...args: unknown[]) =>
    devLogger.warn(`[${prefix}] ${msg}`, ...args),
  error: (msg: string, ...args: unknown[]) =>
    devLogger.error(`[${prefix}] ${msg}`, ...args),
  log: (msg: string, ...args: unknown[]) =>
    devLogger.log(`[${prefix}] ${msg}`, ...args),
});
