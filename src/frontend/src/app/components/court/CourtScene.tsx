/**
 * 宫廷3D场景组件（性能优化版）
 *
 * 主场景容器，包含：
 * - 全员常驻角色渲染（支持LOD）
 * - 镜头系统（总览/聚焦/检查三档）
 * - 性能监控与自适应降级
 * - 环境光效和背景
 * - 场景切换动画
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Stars, Grid, PerspectiveCamera } from '@react-three/drei';
import * as THREE from 'three';
import type { CourtState, CourtTopologyNode, CourtActorState } from '../../types/court';
import { CourtActor3D } from './CourtActor3D';
import { SCENE_NAMES, STATUS_COLORS, RISK_COLORS } from '../../types/court';
import { usePerformanceMonitor, PerformancePanel } from './asset';
import type { LODSettings } from './asset';
import { devLogger } from '@/app/utils/devLogger';

export interface CourtSceneProps {
  courtState: CourtState | null;
  topology: CourtTopologyNode[];
  selectedRoleId: string | null;
  onSelectRole: (roleId: string | null) => void;
  cameraMode?: 'overview' | 'focus' | 'inspect';
  targetRoleId?: string | null;
  enablePerformanceMonitor?: boolean;
  usePlaceholderAssets?: boolean;
}

// 镜头控制器组件
function CameraController({
  mode,
  targetRoleId,
  topology,
  courtState,
}: {
  mode: 'overview' | 'focus' | 'inspect';
  targetRoleId: string | null;
  topology: CourtTopologyNode[];
  courtState: CourtState | null;
}) {
  const { camera } = useThree();
  const targetRef = useRef(new THREE.Vector3(0, 0, 0));
  const positionRef = useRef(new THREE.Vector3(0, 8, 15));

  // 根据模式和目标计算相机位置和焦点
  useEffect(() => {
    const sceneConfig = courtState?.current_scene
      ? (courtState as { current_scene: string }).current_scene
      : 'taiji_hall';

    switch (mode) {
      case 'overview':
        // 总览模式：根据当前场景调整
        if (sceneConfig === 'taiji_hall') {
          positionRef.current.set(0, 10, 18);
          targetRef.current.set(0, 0, 0);
        } else if (sceneConfig === 'zhongshu_pavilion') {
          positionRef.current.set(-6, 6, 12);
          targetRef.current.set(-4, 0, 2);
        } else if (sceneConfig === 'gongbu_blueprint') {
          positionRef.current.set(6, 5, 10);
          targetRef.current.set(6, 0, 6);
        } else {
          positionRef.current.set(0, 8, 15);
          targetRef.current.set(0, 0, 0);
        }
        break;

      case 'focus':
        // 聚焦模式：聚焦到特定角色所在区域
        if (targetRoleId) {
          const node = topology.find((n) => n.role_id === targetRoleId);
          if (node) {
            positionRef.current.set(
              node.position[0] + 3,
              node.position[1] + 4,
              node.position[2] + 6
            );
            targetRef.current.set(...node.position);
          }
        }
        break;

      case 'inspect':
        // 检查模式：近距离观察单个角色
        if (targetRoleId) {
          const node = topology.find((n) => n.role_id === targetRoleId);
          if (node) {
            positionRef.current.set(
              node.position[0] + 1.5,
              node.position[1] + 2,
              node.position[2] + 3
            );
            targetRef.current.set(...node.position);
          }
        }
        break;
    }
  }, [mode, targetRoleId, topology, courtState]);

  // 平滑插值动画
  useFrame(() => {
    camera.position.lerp(positionRef.current, 0.05);
    const currentTarget = new THREE.Vector3();
    camera.getWorldDirection(currentTarget);
    const lookAtTarget = targetRef.current.clone();

    // 使用简单的 lookAt，但保持平滑过渡
    const dummy = new THREE.Object3D();
    dummy.position.copy(camera.position);
    dummy.lookAt(lookAtTarget);
    camera.quaternion.slerp(dummy.quaternion, 0.05);
  });

  return null;
}

// 连接线组件 - 显示层级关系
function ConnectionLines({ topology }: { topology: CourtTopologyNode[] }) {
  const lines = useMemo(() => {
    const result: { start: THREE.Vector3; end: THREE.Vector3; color: string }[] = [];

    topology.forEach((node) => {
      if (node.parent_id) {
        const parent = topology.find((n) => n.role_id === node.parent_id);
        if (parent) {
          result.push({
            start: new THREE.Vector3(...parent.position),
            end: new THREE.Vector3(...node.position),
            color: '#4488aa',
          });
        }
      }
    });

    return result;
  }, [topology]);

  return (
    <group>
      {lines.map((line, index) => (
        <line key={index}>
          <bufferGeometry>
            <bufferAttribute
              attach="attributes-position"
              count={2}
              array={new Float32Array([
                line.start.x, line.start.y, line.start.z,
                line.end.x, line.end.y, line.end.z,
              ])}
              itemSize={3}
            />
          </bufferGeometry>
          <lineBasicMaterial color={line.color} transparent opacity={0.3} />
        </line>
      ))}
    </group>
  );
}

// 场景环境组件
function SceneEnvironment({ shadowQuality }: { shadowQuality: 'high' | 'medium' | 'low' | 'off' }) {
  const shadowMapSize = useMemo(() => {
    switch (shadowQuality) {
      case 'high': return [2048, 2048];
      case 'medium': return [1024, 1024];
      case 'low': return [512, 512];
      default: return undefined;
    }
  }, [shadowQuality]);

  return (
    <>
      {/* 环境光 */}
      <ambientLight intensity={0.3} color="#404060" />

      {/* 主光源 - 模拟殿堂光线 */}
      <directionalLight
        position={[10, 20, 10]}
        intensity={1}
        color="#fff8e7"
        castShadow={shadowQuality !== 'off'}
        shadow-mapSize={shadowMapSize}
      />

      {/* 补光 - 蓝色调增加科技感 */}
      <pointLight position={[-10, 10, -10]} intensity={0.5} color="#4488ff" />
      <pointLight position={[10, 5, -10]} intensity={0.3} color="#ff8844" />

      {/* 星空背景 */}
      <Stars radius={100} depth={50} count={3000} factor={4} saturation={0.5} fade speed={1} />

      {/* 地面网格 */}
      <Grid
        position={[0, -0.1, 0]}
        args={[50, 50]}
        cellSize={1}
        cellThickness={0.5}
        cellColor="#334455"
        sectionSize={5}
        sectionThickness={1}
        sectionColor="#445566"
        fadeDistance={30}
        fadeStrength={1}
        infiniteGrid
      />
    </>
  );
}

export function CourtScene({
  courtState,
  topology,
  selectedRoleId,
  onSelectRole,
  cameraMode = 'overview',
  targetRoleId,
  enablePerformanceMonitor = false,
  usePlaceholderAssets = true,
}: CourtSceneProps) {
  const [localSelected, setLocalSelected] = useState<string | null>(null);

  // 合并外部和内部选择状态
  const effectiveSelectedId = selectedRoleId ?? localSelected;

  // 性能监控
  const { metrics, lodSettings, adaptiveLOD, setAdaptiveLOD } = usePerformanceMonitor(30);

  const handleActorClick = useCallback((roleId: string) => {
    const newSelection = effectiveSelectedId === roleId ? null : roleId;
    setLocalSelected(newSelection);
    onSelectRole?.(newSelection);
  }, [effectiveSelectedId, onSelectRole]);

  // 构建角色状态映射
  const actorMap = useMemo(() => {
    return courtState?.actors ?? {};
  }, [courtState]);

  return (
    <div className="w-full h-full relative bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950">
      {/* 场景标题 */}
      <div className="absolute top-4 left-4 z-10 pointer-events-none">
        <h2 className="text-2xl font-bold text-amber-100/90 drop-shadow-lg">
          {courtState?.current_scene ? SCENE_NAMES[courtState.current_scene] ?? '宫廷' : '宫廷'}
        </h2>
        <p className="text-sm text-amber-200/60 mt-1">
          {courtState?.phase ?? 'court_audience'}
        </p>
      </div>

      {/* 性能监控面板 */}
      {enablePerformanceMonitor && (
        <PerformancePanel
          metrics={metrics}
          lodSettings={lodSettings}
          onToggleAdaptive={() => setAdaptiveLOD(!adaptiveLOD)}
        />
      )}

      {/* 3D Canvas */}
      <Canvas shadows={lodSettings.shadowQuality !== 'off'}>
        <PerspectiveCamera makeDefault fov={60} near={0.1} far={1000} />

        <CameraController
          mode={cameraMode}
          targetRoleId={targetRoleId ?? effectiveSelectedId}
          topology={topology}
          courtState={courtState}
        />

        <SceneEnvironment shadowQuality={lodSettings.shadowQuality} />

        {/* 层级连接线 */}
        <ConnectionLines topology={topology} />

        {/* 角色实体 */}
        <group>
          {topology.map((node) => (
            <CourtActor3D
              key={node.role_id}
              node={node}
              actor={actorMap[node.role_id]}
              isSelected={effectiveSelectedId === node.role_id}
              onClick={() => handleActorClick(node.role_id)}
              lodSettings={lodSettings}
              usePlaceholder={usePlaceholderAssets}
            />
          ))}
        </group>

        {/* 轨道控制器（在 inspect 模式下禁用） */}
        <OrbitControls
          enablePan={cameraMode !== 'inspect'}
          enableZoom={true}
          enableRotate={cameraMode !== 'inspect'}
          minDistance={2}
          maxDistance={50}
          maxPolarAngle={Math.PI / 2 - 0.1}
        />
      </Canvas>

      {/* 操作提示 */}
      <div className="absolute bottom-4 left-4 text-xs text-white/40 pointer-events-none">
        左键点击: 选择角色 | 左键拖拽: 旋转视角 | 滚轮: 缩放 | 右键拖拽: 平移
      </div>

      {/* 选中角色信息面板 */}
      {effectiveSelectedId && actorMap[effectiveSelectedId] && (
        <ActorInfoPanel
          actor={actorMap[effectiveSelectedId]}
          onClose={() => {
            setLocalSelected(null);
            onSelectRole?.(null);
          }}
        />
      )}
    </div>
  );
}

// 角色信息面板
function ActorInfoPanel({
  actor,
  onClose,
}: {
  actor: CourtActorState;
  onClose: () => void;
}) {
  return (
    <div className="absolute top-4 right-4 w-72 bg-slate-900/95 border border-slate-700 rounded-lg shadow-2xl backdrop-blur-md z-20">
      <div className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-bold text-amber-100">{actor.role_name}</h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-slate-400">状态:</span>
            <span
              className="font-medium"
              style={{ color: STATUS_COLORS[actor.status] }}
            >
              {actor.status}
            </span>
          </div>

          <div className="flex justify-between">
            <span className="text-slate-400">当前动作:</span>
            <span className="text-slate-200">{actor.current_action || '-'}</span>
          </div>

          {actor.task_id && (
            <div className="flex justify-between">
              <span className="text-slate-400">任务ID:</span>
              <span className="text-slate-300 font-mono text-xs">{actor.task_id}</span>
            </div>
          )}

          {actor.risk_level !== 'none' && (
            <div className="flex justify-between">
              <span className="text-slate-400">风险等级:</span>
              <span
                className="font-medium"
                style={{ color: RISK_COLORS[actor.risk_level] }}
              >
                {actor.risk_level}
              </span>
            </div>
          )}

          {actor.evidence_refs.length > 0 && (
            <div className="mt-3">
              <span className="text-slate-400 block mb-2">证据链:</span>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {actor.evidence_refs.map((ref, index) => (
                  <button
                    key={index}
                    className="block w-full text-left px-2 py-1 bg-slate-800/50 rounded text-xs text-cyan-300 hover:bg-slate-700/50 transition-colors truncate"
                    onClick={() => {
                      // TODO: 跳转到证据详情
                      devLogger.debug('Navigate to evidence:', ref);
                    }}
                  >
                    {ref.path}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default CourtScene;
