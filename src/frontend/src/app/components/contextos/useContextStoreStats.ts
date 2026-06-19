/**
 * useContextStoreStats — 拉取 /v2/context/admin/stats 的轻量 hook。
 *
 * 设计目标：
 *  - 默认每 30s 拉取一次（与后端 sweep_min_interval_seconds 默认 300s 错位，避免和 sweep 撞车）；
 *  - 若 admin 端点返回 404 / ADMIN_DISABLED → stats-disabled 状态（不视为错误，静默显示提示）；
 *  - 任何其他失败 → error 状态，保留上次成功数据便于渲染；
 *  - 手动刷新：返回的 refresh 函数触发立即拉取；
 *  - cleanup：组件卸载或 workspace 切换时取消在飞请求 + 清空定时器（fail-closed）。
 *
 * 注意：本 hook 是**只读**的——不调用 sweep，不修改后端状态。强制 sweep 由用户
 * 在面板上手动触发后由后端 admin 端点处理（sweep 按钮已接入 POST /v2/context/admin/sweep）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { apiFetch } from '@/api';

import {
  parseContextStoreStatsResponse,
  type ContextStoreStatsResponse,
} from './contextosStoreStats';

export type StatsFetchState =
  | { kind: 'idle' }
  | { kind: 'loading'; previous: ContextStoreStatsResponse | null }
  | { kind: 'ready'; data: ContextStoreStatsResponse }
  | { kind: 'disabled'; reason: string }
  | { kind: 'error'; message: string; previous: ContextStoreStatsResponse | null };

export interface UseContextStoreStatsResult {
  state: StatsFetchState;
  refresh: () => void;
  /** POST /v2/context/admin/sweep — 触发强制 sweep，成功后把返回报告合并进最近一次 stats。 */
  triggerSweep: () => Promise<{ ok: boolean; error: string | null }>;
}

/** 默认拉取间隔。错位 sweep_min_interval_seconds（300s）→ 30s 一次的低频轮询对 on-read gate 无影响。 */
const DEFAULT_POLL_INTERVAL_MS = 30_000;

/** 单次请求超时。后端 admin stats 是 cheap stat snapshot，10s 足矣。 */
const REQUEST_TIMEOUT_MS = 10_000;

interface ErrorPayload {
  code?: string;
  message?: string;
}

function readErrorPayload(body: string): ErrorPayload {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      const detail = obj.detail && typeof obj.detail === 'object' ? (obj.detail as Record<string, unknown>) : null;
      return {
        code: typeof obj.code === 'string' ? obj.code : typeof detail?.code === 'string' ? detail.code : undefined,
        message: typeof obj.message === 'string' ? obj.message : typeof detail?.message === 'string' ? detail.message : undefined,
      };
    }
  } catch {
    // fall through
  }
  return {};
}

export function useContextStoreStats(options: {
  workspace?: string | null;
  pollIntervalMs?: number;
  enabled?: boolean;
}): UseContextStoreStatsResult {
  const { workspace, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS, enabled = true } = options;

  const [state, setState] = useState<StatsFetchState>({ kind: 'idle' });
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);
  /** 持续追踪「最近一次成功响应」——错误态用它做前次数据回填。
   * 之所以需要单独 ref：setState({kind:'loading'}) 后，prev 不再是 ready，
   * 而错误态又依赖 prev.kind === 'ready' 才能拿到数据，导致 previous 丢失。 */
  const lastGoodRef = useRef<ContextStoreStatsResponse | null>(null);

  const fetchOnce = useCallback(async () => {
    // 取消上一次在飞请求（fail-closed：旧的过期响应丢弃）。
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((prev) => ({ kind: 'loading', previous: prev.kind === 'ready' ? prev.data : lastGoodRef.current }));

    let response: Response;
    try {
      response = await apiFetch('/v2/context/admin/stats', {
        method: 'GET',
        signal: controller.signal,
        timeout: REQUEST_TIMEOUT_MS,
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
        previous: lastGoodRef.current,
      });
      return;
    }

    if (controller.signal.aborted) return;

    // 404 / ADMIN_DISABLED → disabled 状态（不是错误，端点就是 opt-in）。
    if (response.status === 404) {
      const text = await response.text().catch(() => '');
      const payload = readErrorPayload(text);
      const reason = payload.message || payload.code || 'Context admin surface is disabled';
      setState({ kind: 'disabled', reason });
      return;
    }

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      setState({
        kind: 'error',
        message: `HTTP ${response.status} ${text.slice(0, 200) || response.statusText}`,
        previous: lastGoodRef.current,
      });
      return;
    }

    try {
      const body = (await response.json()) as unknown;
      const parsed = parseContextStoreStatsResponse(body);
      if (!parsed) {
        setState({
          kind: 'error',
          message: 'Malformed stats response',
          previous: lastGoodRef.current,
        });
        return;
      }
      lastGoodRef.current = parsed;
      setState({ kind: 'ready', data: parsed });
    } catch (err) {
      if (controller.signal.aborted) return;
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
        previous: lastGoodRef.current,
      });
    }
  }, []);

  // 主 effect：启动 / 暂停轮询；workspace 切换时立即拉一次新数据。
  useEffect(() => {
    if (!enabled) {
      // 关闭时静默清空状态——保留之前的"最近一次"会误导用户。
      abortRef.current?.abort();
      lastGoodRef.current = null;
      setState({ kind: 'idle' });
      return;
    }
    void fetchOnce();
    timerRef.current = window.setInterval(() => {
      void fetchOnce();
    }, pollIntervalMs);
    return () => {
      abortRef.current?.abort();
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [workspace, enabled, pollIntervalMs, fetchOnce]);

  const triggerSweep = useCallback(async (): Promise<{ ok: boolean; error: string | null }> => {
    try {
      const response = await apiFetch('/v2/context/admin/sweep', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ triggers: ['admin-ui'] }),
        timeout: REQUEST_TIMEOUT_MS,
      });
      if (!response.ok) {
        const text = await response.text().catch(() => '');
        return { ok: false, error: `HTTP ${response.status} ${text.slice(0, 200) || response.statusText}` };
      }
      // sweep 成功后立即拉一次最新 stats。
      void fetchOnce();
      return { ok: true, error: null };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }, [fetchOnce]);

  return { state, refresh: fetchOnce, triggerSweep };
}