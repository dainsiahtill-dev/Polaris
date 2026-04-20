/**
 * 宫廷角色3D实体组件（正式资产版）
 *
 * 支持 GLB/GLTF 资产加载、动画混合、LOD切换
 */

import React, { useRef, useState, useMemo, useEffect } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Html, Billboard } from '@react-three/drei';
import * as THREE from 'three';
import type { CourtActorState, CourtTopologyNode } from '../../types/court';
import { STATUS_COLORS, RISK_COLORS } from '../../types/court';
import { useRoleAsset, useRoleAnimation, calculateLODLevel } from './asset';
import type { LODSettings } from './asset';

interface CourtActor3DProps {
  node: CourtTopologyNode;
  actor?: CourtActorState;
  isSelected: boolean;
  onClick: () => void;
  lodSettings: LODSettings;
  usePlaceholder?: boolean; // 是否使用占位模型（资产未加载完成时）
}

// 占位几何体缓存
const geometryCache = new Map<string, THREE.BufferGeometry>();

function getPlaceholderGeometry(department: string): THREE.BufferGeometry {
  if (!geometryCache.has(department)) {
    let geometry: THREE.BufferGeometry;

    switch (department) {
      case 'imperial':
        geometry = new THREE.CylinderGeometry(0.5, 0.6, 1.5, 8);
        break;
      case 'zhongshu':
        geometry = new THREE.BoxGeometry(0.8, 1, 0.8);
        break;
      case 'menxia':
        geometry = new THREE.OctahedronGeometry(0.6);
        break;
      case 'shangshu':
        geometry = new THREE.DodecahedronGeometry(0.6);
        break;
      case 'gongbu':
        geometry = new THREE.ConeGeometry(0.5, 1.2, 6);
        break;
      default:
        geometry = new THREE.SphereGeometry(0.5, 16, 16);
    }

    geometryCache.set(department, geometry);
  }
  return geometryCache.get(department)!;
}

export function CourtActor3D({
  node,
  actor,
  isSelected,
  onClick,
  lodSettings,
  usePlaceholder = false,
}: CourtActor3DProps) {
  const groupRef = useRef<THREE.Group>(null);
  const [hovered, setHovered] = useState(false);
  const { camera } = useThree();

  // 计算到相机的距离
  const distance = useMemo(() => {
    const pos = new THREE.Vector3(...node.position);
    return pos.distanceTo(camera.position);
  }, [node.position, camera.position]);

  // 计算应使用的 LOD 级别
  const targetLOD = useMemo(() => {
    return calculateLODLevel(distance, lodSettings.currentLevel, lodSettings.currentLevel);
  }, [distance, lodSettings.currentLevel]);

  // 加载角色资产
  const { asset, isLoading } = useRoleAsset(node.role_id, targetLOD);

  // 动画管理
  const { mixer, currentAnimation } = useRoleAnimation(
    asset,
    actor?.status || 'idle',
    lodSettings.enableAnimations
  );

  // 更新动画混合器
  useFrame((state, delta) => {
    if (mixer && lodSettings.enableAnimations) {
      mixer.update(delta);
    }

    if (!groupRef.current) return;

    // 状态动画效果
    if (actor?.status === 'executing' || actor?.status === 'dispatching') {
      groupRef.current.position.y = node.position[1] + Math.sin(state.clock.elapsedTime * 3) * 0.1;
    } else if (actor?.status === 'thinking') {
      groupRef.current.rotation.y = Math.sin(state.clock.elapsedTime) * 0.1;
    } else if (actor?.status === 'blocked' || actor?.status === 'failed') {
      const pulse = (Math.sin(state.clock.elapsedTime * 5) + 1) * 0.5;
      groupRef.current.scale.setScalar(1 + pulse * 0.1);
    } else {
      // 恢复默认
      groupRef.current.position.y = THREE.MathUtils.lerp(
        groupRef.current.position.y,
        node.position[1],
        0.1
      );
      groupRef.current.scale.lerp(new THREE.Vector3(1, 1, 1), 0.1);
    }
  });

  // 根据状态确定颜色
  const baseColor = useMemo(() => {
    if (!actor) return '#888888';
    return STATUS_COLORS[actor.status] || '#888888';
  }, [actor]);

  const riskColor = useMemo(() => {
    if (!actor || actor.risk_level === 'none') return null;
    return RISK_COLORS[actor.risk_level];
  }, [actor]);

  // 渲染占位模型或正式资产
  const renderContent = () => {
    if (usePlaceholder || isLoading || !asset) {
      // 使用占位几何体
      const geometry = getPlaceholderGeometry(node.department);
      return (
        <mesh geometry={geometry}>
          <meshStandardMaterial
            color={baseColor}
            emissive={riskColor || baseColor}
            emissiveIntensity={hovered ? 0.8 : riskColor ? 0.5 : 0.2}
            metalness={0.6}
            roughness={0.4}
          />
        </mesh>
      );
    }

    // 使用正式资产
    return (
      <primitive
        object={asset.scene.clone()}
        scale={1.5}
      />
    );
  };

  return (
    <group
      ref={groupRef}
      position={node.position}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHovered(true);
      }}
      onPointerOut={() => setHovered(false)}
    >
      {/* 选中时的高亮环 */}
      {isSelected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0]}>
          <ringGeometry args={[0.8, 1, 32]} />
          <meshBasicMaterial color="#00ffff" transparent opacity={0.8} />
        </mesh>
      )}

      {/* 角色主体 */}
      {renderContent()}

      {/* 状态指示灯 */}
      <Billboard position={[0.6, 0.8, 0]}>
        <mesh>
          <sphereGeometry args={[0.15, 8, 8]} />
          <meshBasicMaterial color={baseColor} />
        </mesh>
      </Billboard>

      {/* LOD 指示器（调试模式） */}
      {process.env.NODE_ENV === 'development' && (
        <Html distanceFactor={10} position={[0, -0.5, 0]}>
          <div className="text-[10px] text-white/50 bg-black/50 px-1 rounded">
            LOD{targetLOD}
          </div>
        </Html>
      )}

      {/* 悬浮提示 */}
      {(hovered || isSelected) && (
        <Html distanceFactor={10} position={[0, 1.5, 0]}>
          <div
            className={`
              px-3 py-2 rounded-lg border backdrop-blur-md shadow-lg
              ${isSelected ? 'bg-cyan-950/90 border-cyan-400' : 'bg-black/80 border-white/30'}
              min-w-[140px]
            `}
          >
            <div className="text-sm font-bold text-white">
              {node.role_name}
            </div>
            {asset && (
              <div className="text-[10px] text-slate-400 mt-0.5">
                Anim: {currentAnimation || 'idle'}
              </div>
            )}
            {actor && (
              <>
                <div
                  className="text-xs mt-1"
                  style={{ color: baseColor }}
                >
                  {actor.current_action || actor.status}
                </div>
                {actor.risk_level !== 'none' && (
                  <div
                    className="text-xs mt-1"
                    style={{ color: RISK_COLORS[actor.risk_level] }}
                  >
                    风险: {actor.risk_level}
                  </div>
                )}
              </>
            )}
          </div>
        </Html>
      )}
    </group>
  );
}

export default CourtActor3D;
