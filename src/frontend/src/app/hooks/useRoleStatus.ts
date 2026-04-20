/**
 * usePmStatus - PM (Project Manager) 状态管理 Hook
 *
 * 职责:
 * - 订阅 PM 状态更新
 * - 提供 PM 相关数据访问
 */

import { useCallback, useEffect } from 'react';
import { useRuntimeStore } from './useRuntimeStore';
import { useRuntimeTransport } from '@/runtime/transport';
import type { BackendStatus } from '@/app/types/appContracts';
import * as Parsing from './runtimeParsing';

/**
 * usePmStatus - 管理 PM 状态
 *
 * @returns pmStatus - 当前 PM 状态
 * @returns setPmStatus - 手动设置 PM 状态 (由 useRuntime message handler 调用)
 */
export function usePmStatus() {
  const pmStatus = useRuntimeStore((s) => s.pmStatus);
  const setPmStatus = useRuntimeStore((s) => s.setPmStatus);

  return {
    pmStatus,
    setPmStatus,
  };
}

/**
 * usePmStatusSync - 同步 PM 状态到 store
 *
 * 供 useRuntime 内部的 processMessage 调用
 */
export function usePmStatusSync() {
  const setPmStatus = useRuntimeStore((s) => s.setPmStatus);

  const syncPmStatus = useCallback(
    (payload: { pm_status?: BackendStatus | null }) => {
      setPmStatus(payload.pm_status ?? null);
    },
    [setPmStatus]
  );

  return { syncPmStatus };
}

/**
 * useDirectorStatus - Director 状态管理 Hook
 */
export function useDirectorStatus() {
  const directorStatus = useRuntimeStore((s) => s.directorStatus);
  const setDirectorStatus = useRuntimeStore((s) => s.setDirectorStatus);

  return {
    directorStatus,
    setDirectorStatus,
  };
}

/**
 * useDirectorStatusSync - 同步 Director 状态到 store
 *
 * 供 useRuntime 内部的 processMessage 调用
 */
export function useDirectorStatusSync() {
  const setDirectorStatus = useRuntimeStore((s) => s.setDirectorStatus);

  const syncDirectorStatus = useCallback(
    (payload: { director_status?: BackendStatus | null }) => {
      setDirectorStatus(payload.director_status ?? null);
    },
    [setDirectorStatus]
  );

  return { syncDirectorStatus };
}

/**
 * useRoleStatus - 综合角色状态管理 Hook
 *
 * 导出所有角色状态相关的 hooks
 */
export function useRoleStatus() {
  const pmStatus = useRuntimeStore((s) => s.pmStatus);
  const directorStatus = useRuntimeStore((s) => s.directorStatus);
  const setPmStatus = useRuntimeStore((s) => s.setPmStatus);
  const setDirectorStatus = useRuntimeStore((s) => s.setDirectorStatus);

  return {
    pmStatus,
    directorStatus,
    setPmStatus,
    setDirectorStatus,
  };
}