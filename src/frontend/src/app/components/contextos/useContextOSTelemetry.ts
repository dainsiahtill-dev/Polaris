/**
 * useContextOSTelemetry — ContextOS 实时遥测轮询 Hook
 *
 * 读取真实运行时观测日志 `runtime/events/llm.observations.jsonl`，解析成
 * ContextOS 遥测模型，并以较快节奏轮询（默认 4s，比全局 UsageHUD 的 30s 更实时），
 * 仅在 ContextOS 视图挂载期间工作。
 *
 * 设计要点：
 *  - 复用既有 `readFile` 服务与既有逻辑路径常量（不新建后端端点 / 不重复造轮子）。
 *  - 失败 / 空文件优雅降级为 EMPTY_TELEMETRY，绝不抛出。
 *  - 卸载即清理定时器；workspace 变化即重置。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { readFile } from '@/services/fileService';
import { LLM_OBSERVATIONS_LOGICAL_PATH } from '@/app/hooks/useUsageStats';
import {
  EMPTY_TELEMETRY,
  parseObservationLog,
  type ContextOSTelemetry,
} from './contextOSTelemetry';

export interface UseContextOSTelemetryResult {
  telemetry: ContextOSTelemetry;
  loading: boolean;
  error: string | null;
  /** 最近一次成功读取的本地时间戳（epoch ms），无则 null。 */
  lastUpdated: number | null;
  refresh: () => void;
}

/** 观测日志尾部读取行数（覆盖足够的近期事件）。 */
const TAIL_LINES = 800;

export function useContextOSTelemetry(
  workspace: string | null,
  options: { pollMs?: number; enabled?: boolean } = {},
): UseContextOSTelemetryResult {
  const { pollMs = 4000, enabled = true } = options;

  const [telemetry, setTelemetry] = useState<ContextOSTelemetry>(EMPTY_TELEMETRY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  // 避免组件卸载后 setState。
  const mountedRef = useRef(true);
  // 每次发起读取都自增的"代次"，用于丢弃被更新请求取代的过期响应
  // （workspace 切换 / 手动刷新与轮询重叠时，旧/慢响应不得覆盖新数据）。
  const runIdRef = useRef(0);

  const fetchTelemetry = useCallback(async () => {
    if (!workspace || !enabled) {
      runIdRef.current += 1; // 作废任何在途请求
      setTelemetry(EMPTY_TELEMETRY);
      return;
    }
    const myRun = ++runIdRef.current;
    // 仅当本次仍是最新一代且组件已挂载时才允许写状态。
    const isCurrent = () => mountedRef.current && myRun === runIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await readFile(LLM_OBSERVATIONS_LOGICAL_PATH, TAIL_LINES);
      if (!isCurrent()) return;
      if (!result.ok || !result.data) {
        setTelemetry(EMPTY_TELEMETRY);
        return;
      }
      const content = (result.data.content as string | undefined) ?? '';
      const parsed = parseObservationLog(content);
      setTelemetry(parsed);
      setLastUpdated(Date.now());
    } catch (err) {
      if (!isCurrent()) return;
      setError(err instanceof Error ? err.message : 'Failed to read ContextOS telemetry');
      setTelemetry(EMPTY_TELEMETRY);
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [workspace, enabled]);

  useEffect(() => {
    mountedRef.current = true;
    if (!workspace || !enabled) {
      setTelemetry(EMPTY_TELEMETRY);
      return () => {
        mountedRef.current = false;
      };
    }

    void fetchTelemetry();
    const interval = setInterval(() => {
      void fetchTelemetry();
    }, Math.max(1000, pollMs));

    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [workspace, enabled, pollMs, fetchTelemetry]);

  return { telemetry, loading, error, lastUpdated, refresh: fetchTelemetry };
}
