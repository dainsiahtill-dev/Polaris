/**
 * Court Role Entity Component
 *
 * Render a single court role in 3D scene，支持状态颜色、动画和交互
 */

import React, { useRef, useState, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html, Billboard } from '@react-three/drei';
import * as THREE from 'three';
import type { CourtActorState, CourtTopologyNode } from '../../types/court';
import { STATUS_COLORS, RISK_COLORS } from '../../types/court';

export interface CourtActorProps {
  node: CourtTopologyNode;
  actor?: CourtActorState;
  isSelected: boolean;
  onClick: () => void;
}

// Role geometry cache
const geometryCache = new Map<string, THREE.BufferGeometry>();

function getGeometry(department: string): THREE.BufferGeometry {
  if (!geometryCache.has(department)) {
    let geometry: THREE.BufferGeometry;

    // Create different shapes based on department
    switch (department) {
      case 'imperial': // User - Cylinder (Platform)
        geometry = new THREE.CylinderGeometry(0.5, 0.6, 1.5, 8);
        break;
      case 'zhongshu': // Architect Office - Cube
        geometry = new THREE.BoxGeometry(0.8, 1, 0.8);
        break;
      case 'menxia': // QA Office - Octahedron
        geometry = new THREE.OctahedronGeometry(0.6);
        break;
      case 'shangshu': // PM Office - Dodecahedron
        geometry = new THREE.DodecahedronGeometry(0.6);
        break;
      case 'gongbu': // Engineering - Cone (Construction)
        geometry = new THREE.ConeGeometry(0.5, 1.2, 6);
        break;
      default: // 其他部门 - 球体
        geometry = new THREE.SphereGeometry(0.5, 16, 16);
    }

    geometryCache.set(department, geometry);
  }
  return geometryCache.get(department)!;
}

export function CourtActor({ node, actor, isSelected, onClick }: CourtActorProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  // 根据状态确定颜色
  const baseColor = useMemo(() => {
    if (!actor) return '#888888';
    return STATUS_COLORS[actor.status] || '#888888';
  }, [actor]);

  // 风险等级发光颜色
  const riskColor = useMemo(() => {
    if (!actor || actor.risk_level === 'none') return null;
    return RISK_COLORS[actor.risk_level];
  }, [actor]);

  // 动画效果
  useFrame((state) => {
    if (!meshRef.current) return;

    // 根据状态添加动画
    if (actor?.status === 'executing' || actor?.status === 'dispatching') {
      // 执行中：上下浮动
      meshRef.current.position.y = node.position[1] + Math.sin(state.clock.elapsedTime * 3) * 0.1;
    } else if (actor?.status === 'thinking') {
      // 思考中：轻微旋转
      meshRef.current.rotation.y = Math.sin(state.clock.elapsedTime) * 0.1;
    } else if (actor?.status === 'blocked' || actor?.status === 'failed') {
      // 阻塞/失败：红色脉冲
      const pulse = (Math.sin(state.clock.elapsedTime * 5) + 1) * 0.5;
      meshRef.current.scale.setScalar(1 + pulse * 0.1);
    } else {
      // 恢复默认位置和缩放
      meshRef.current.position.y = THREE.MathUtils.lerp(
        meshRef.current.position.y,
        node.position[1],
        0.1
      );
      meshRef.current.scale.lerp(new THREE.Vector3(1, 1, 1), 0.1);
    }
  });

  const geometry = useMemo(() => getGeometry(node.department), [node.department]);

  return (
    <group position={node.position}>
      {/* 选中时的高亮环 */}
      {isSelected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0]}>
          <ringGeometry args={[0.8, 1, 32]} />
          <meshBasicMaterial color="#00ffff" transparent opacity={0.8} />
        </mesh>
      )}

      {/* 角色主体 */}
      <mesh
        ref={meshRef}
        geometry={geometry}
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
        <meshStandardMaterial
          color={baseColor}
          emissive={riskColor || baseColor}
          emissiveIntensity={hovered ? 0.8 : riskColor ? 0.5 : 0.2}
          metalness={0.6}
          roughness={0.4}
        />
      </mesh>

      {/* 状态指示灯 */}
      <Billboard position={[0.6, 0.8, 0]}>
        <mesh>
          <sphereGeometry args={[0.15, 8, 8]} />
          <meshBasicMaterial color={baseColor} />
        </mesh>
      </Billboard>

      {/* 悬浮提示 */}
      {(hovered || isSelected) && (
        <Html distanceFactor={10} position={[0, 1.5, 0]}>
          <div
            className={`
              px-3 py-2 rounded-lg border backdrop-blur-md shadow-lg
              ${isSelected ? 'bg-cyan-950/90 border-cyan-400' : 'bg-black/80 border-white/30'}
              min-w-[120px]
            `}
          >
            <div className="text-sm font-bold text-white">
              {node.role_name}
            </div>
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
                    Risk: {actor.risk_level}
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

export default CourtActor;
