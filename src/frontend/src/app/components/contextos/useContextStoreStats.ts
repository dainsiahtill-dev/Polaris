/**
 * useContextStoreStats — 拉取 /v2/context/admin/stats 的轻量 hook。
 *
 * 设计目标：
 *  - 组件挂载 / workspace 切换时读取一次快照；
 *  - 若 admin 端点返回 404 / ADMIN_DISABLED → stats-disabled 状态（不视为错误，静默显示提示）；
 *  - 任何其他失败 → error 状态，保留上次成功数据便于渲染；
 *  - 手动刷新：返回的 refresh 函数触发立即拉取；
 *  - cleanup：组件卸载或 workspace 切换时取消在飞请求（fail-closed）。
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
  | { kind: 'ready'; data: ContextStoreStatsResponse; isAdmin: boolean }
  | { kind: 'disabled'; reason: string }
  | { kind: 'error'; message: string; previous: ContextStoreStatsResponse | null };

export interface UseContextStoreStatsResult {
  state: StatsFetchState;
  refresh: () => void;
  /** POST /v2/context/admin/sweep — 触发强制 sweep，成功后把返回报告合并进最近一次 stats。 */
  triggerSweep: () => Promise<{ ok: boolean; error: string | null }>;
}

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

function isAbortLikeError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const error = err as { name?: unknown; message?: unknown };
  if (error.name === 'AbortError') return true;
  const message = typeof error.message === 'string' ? error.message.toLowerCase() : '';
  return message.includes('signal is aborted') || message.includes('operation was aborted');
}

export function useContextStoreStats(options: {
  workspace?: string | null;
  enabled?: boolean;
}): UseContextStoreStatsResult {
  const { workspace, enabled = true } = options;

  const [state, setState] = useState<StatsFetchState>({ kind: 'idle' });
  const abortRef = useRef<AbortController | null>(null);
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

    // 先尝试 admin 端点
    let response: Response;
    let isAdmin = true;
    try {
      response = await apiFetch('/v2/context/admin/stats', {
        method: 'GET',
        signal: controller.signal,
        timeout: REQUEST_TIMEOUT_MS,
      });
    } catch (err) {
      if (controller.signal.aborted || isAbortLikeError(err)) return;
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
        previous: lastGoodRef.current,
      });
      return;
    }

    if (controller.signal.aborted) return;

    // 404 / ADMIN_DISABLED → 尝试基本 stats 端点
    if (response.status === 404) {
      const adminText = await response.text().catch(() => '');
      const adminPayload = readErrorPayload(adminText);
      const adminReason = adminPayload.message || adminPayload.code || 'Context admin surface is disabled';

      try {
        response = await apiFetch('/v2/context/stats', {
          method: 'GET',
          signal: controller.signal,
          timeout: REQUEST_TIMEOUT_MS,
        });
        isAdmin = false;
      } catch (err) {
        if (controller.signal.aborted || isAbortLikeError(err)) return;
        // 如果基本端点也失败，显示 disabled 状态
        setState({ kind: 'disabled', reason: adminReason });
        return;
      }

      // 如果基本端点也返回 404，显示 disabled 状态
      if (response.status === 404) {
        setState({ kind: 'disabled', reason: adminReason });
        return;
      }
    }

    if (controller.signal.aborted) return;

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
      setState({ kind: 'ready', data: parsed, isAdmin });
    } catch (err) {
      if (controller.signal.aborted) return;
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
        previous: lastGoodRef.current,
      });
    }
  }, []);

  // 主 effect：workspace 切换时读取一次快照；后续由显式 refresh 驱动。
  useEffect(() => {
    if (!enabled) {
      // 关闭时静默清空状态——保留之前的"最近一次"会误导用户。
      abortRef.current?.abort();
      lastGoodRef.current = null;
      setState({ kind: 'idle' });
      return;
    }
    void fetchOnce();
    return () => {
      abortRef.current?.abort();
    };
  }, [workspace, enabled, fetchOnce]);

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
      if (isAbortLikeError(err)) return { ok: false, error: null };
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }, [fetchOnce]);

  return { state, refresh: fetchOnce, triggerSweep };
}
