/**
 * 宫廷投影容器组件
 *
 * 整合数据获取、WebSocket 实时更新和场景渲染
 */

import React, { useState, useMemo, useCallback } from 'react';
import { CourtScene } from './CourtScene';
import { useCourtTopology, useCourtState, useCourtWebSocket } from '../../hooks/useCourt';
import type { CourtTopologyNode, CourtActorState } from '../../types/court';

interface CourtContainerProps {
  /** 初始相机模式 */
  defaultCameraMode?: 'overview' | 'focus' | 'inspect';
  /** 角色选择回调 */
  onActorSelect?: (actor: CourtActorState | null) => void;
  /** 是否启用 WebSocket 实时更新 */
  enableRealtime?: boolean;
}

export function CourtContainer({
  defaultCameraMode = 'overview',
  onActorSelect,
  enableRealtime = true,
}: CourtContainerProps) {
  // 获取拓扑结构（静态，只获取一次）
  const { topology: topologyData, loading: topologyLoading } = useCourtTopology();

  // 获取初始状态（HTTP轮询，作为fallback）
  const { state: pollState } = useCourtState(enableRealtime ? 30000 : 3000);

  // WebSocket 实时状态
  const { state: wsState, connected: wsConnected } = useCourtWebSocket();

  // 优先使用 WebSocket 状态，否则使用轮询状态
  const courtState = useMemo(() => {
    return wsState ?? pollState;
  }, [wsState, pollState]);

  // 相机模式和选中的角色
  const [cameraMode, setCameraMode] = useState(defaultCameraMode);
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);

  // 拓扑节点列表
  const topology: CourtTopologyNode[] = useMemo(() => {
    return topologyData?.nodes ?? [];
  }, [topologyData]);

  // 角色选择处理
  const handleSelectRole = useCallback((roleId: string | null) => {
    setSelectedRoleId(roleId);

    if (onActorSelect) {
      const actor = roleId ? courtState?.actors?.[roleId] ?? null : null;
      onActorSelect(actor);
    }

    // 选择角色时自动切换到 inspect 模式
    if (roleId && cameraMode === 'overview') {
      setCameraMode('focus');
    }
  }, [courtState, onActorSelect, cameraMode]);

  // 切换相机模式
  const handleCameraModeChange = useCallback((mode: 'overview' | 'focus' | 'inspect') => {
    setCameraMode(mode);
    if (mode === 'overview') {
      setSelectedRoleId(null);
      onActorSelect?.(null);
    }
  }, [onActorSelect]);

  if (topologyLoading) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-amber-500/30 border-t-amber-500 rounded-full animate-spin mx-auto mb-4" />
          <p className="text-amber-200/60">加载宫廷场景...⛩️</p>
        </div>
      </div>
    );
  }

  if (!topology.length) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950">
        <div className="text-center text-red-400">
          <p>加载宫廷拓扑失败</p>
          <p className="text-sm text-red-400/60 mt-2">请检查网络连接并刷新页面</p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col">
      {/* 控制栏 */}
      <div className="h-12 bg-slate-900/80 border-b border-slate-700 flex items-center px-4 justify-between">
        <div className="flex items-center gap-4">
          <span className="text-amber-100 font-medium">宫廷投影</span>

          {/* 连接状态指示 */}
          <div className="flex items-center gap-2 text-xs">
            <div
              className={`w-2 h-2 rounded-full ${
                wsConnected ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'
              }`}
            />
            <span className={wsConnected ? 'text-green-400' : 'text-yellow-400'}>
              {wsConnected ? '实时' : '轮询'}
            </span>
          </div>
        </div>

        {/* 镜头模式切换 */}
        <div className="flex items-center gap-2">
          {(['overview', 'focus', 'inspect'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => handleCameraModeChange(mode)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                cameraMode === mode
                  ? 'bg-amber-600 text-white'
                  : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
              }`}
            >
              {mode === 'overview' && '总览'}
              {mode === 'focus' && '聚焦'}
              {mode === 'inspect' && '检查'}
            </button>
          ))}
        </div>
      </div>

      {/* 场景 */}
      <div className="flex-1">
        <CourtScene
          courtState={courtState}
          topology={topology}
          selectedRoleId={selectedRoleId}
          onSelectRole={handleSelectRole}
          cameraMode={cameraMode}
          targetRoleId={selectedRoleId}
        />
      </div>
    </div>
  );
}

export default CourtContainer;
