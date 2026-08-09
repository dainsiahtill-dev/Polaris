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
const isDev = import.meta.env.DEV;
const isTest = import.meta.env.MODE === 'test';
const forceDebug = import.meta.env.VITE_POLARIS_FORCE_DEBUG === '1';
const isDebugDisabled = () => {
    try {
        return localStorage.getItem('polaris:debug:disable') === 'true';
    }
    catch {
        return false;
    }
};
const shouldLog = (level = 'debug') => {
    if (level === 'error')
        return true; // error 始终输出
    if (isTest)
        return false;
    return (forceDebug && !isDebugDisabled()) || isDev;
};
/**
 * 核心日志方法
 */
export const devLogger = {
    debug: (message, ...args) => {
        if (shouldLog('debug'))
            console.debug(`[DEBUG] ${message}`, ...args);
    },
    info: (message, ...args) => {
        if (shouldLog('info'))
            console.info(`[INFO] ${message}`, ...args);
    },
    warn: (message, ...args) => {
        if (shouldLog('warn'))
            console.warn(`[WARN] ${message}`, ...args);
        // TODO: 生产环境可选上报到监控服务
    },
    error: (message, ...args) => {
        console.error(`[ERROR] ${message}`, ...args);
        // TODO: 可选上报到错误监控服务
    },
    log: (message, ...args) => {
        if (shouldLog('debug'))
            console.log(`[LOG] ${message}`, ...args);
    },
};
/**
 * 创建带前缀的 logger 实例
 * @param prefix 日志前缀，如组件名
 * @returns Logger instance
 */
export const createLogger = (prefix) => ({
    debug: (msg, ...args) => devLogger.debug(`[${prefix}] ${msg}`, ...args),
    info: (msg, ...args) => devLogger.info(`[${prefix}] ${msg}`, ...args),
    warn: (msg, ...args) => devLogger.warn(`[${prefix}] ${msg}`, ...args),
    error: (msg, ...args) => devLogger.error(`[${prefix}] ${msg}`, ...args),
    log: (msg, ...args) => devLogger.log(`[${prefix}] ${msg}`, ...args),
});
