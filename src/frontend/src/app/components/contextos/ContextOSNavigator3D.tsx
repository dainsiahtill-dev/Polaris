/** Opt-in 3D exploration of the same live ContextOS nodes.
 *
 * The 3D view trades readability for spatial overview, so it is never the
 * default: 2D keeps the readable layered form and the table fallback.
 */
import { useMemo, useState, type ReactElement } from 'react';
import { Canvas, type ThreeEvent } from '@react-three/fiber';
import { Html, Line, OrbitControls } from '@react-three/drei';
import {
  LIVE_FOLD_LABELS,
  LIVE_KIND_LABELS,
  type LiveContextEdge,
  type LiveContextNode,
  type LiveFoldState,
} from './contextOSNavigatorModel';

const FOLD_COLOR: Record<LiveFoldState, string> = {
  ACTIVE: '#0ca30c',
  FOLDED: '#ec835a',
  REFERENCE: '#898781',
  EVICTED: '#383835',
  UNKNOWN: '#fab219',
};

const MAX_NODES = 80;
const LAYER_SPACING = 26;
const ROW_SPACING = 9;

interface Point3 { x: number; y: number; z: number }

function hashOf(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) % 9973;
  return hash;
}

export interface ContextOSNavigator3DProps {
  nodes: LiveContextNode[];
  edges: LiveContextEdge[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function NodePoint({ node, position, selected, onSelect }: {
  node: LiveContextNode;
  position: Point3;
  selected: boolean;
  onSelect: (id: string) => void;
}): ReactElement {
  const [hovered, setHovered] = useState(false);
  const color = FOLD_COLOR[node.foldState];
  return (
    <mesh
      position={[position.x, position.y, position.z]}
      onClick={(event: ThreeEvent<MouseEvent>) => { event.stopPropagation(); onSelect(node.id); }}
      onPointerOver={(event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); setHovered(true); }}
      onPointerOut={() => setHovered(false)}
    >
      <sphereGeometry args={[selected ? 2.6 : hovered ? 2.1 : 1.5, 16, 16]} />
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={selected ? 0.9 : hovered ? 0.6 : 0.25} />
      {hovered || selected ? (
        <Html distanceFactor={140} position={[0, 3.2, 0]} style={{ pointerEvents: 'none' }}>
          <div className="whitespace-nowrap rounded bg-[#141413] px-1.5 py-0.5 text-[9px] text-[#f0efec] shadow">
            {node.title}
            <span className="ml-1 text-[#898781]">{LIVE_KIND_LABELS[node.kind]}</span>
          </div>
        </Html>
      ) : null}
    </mesh>
  );
}

export function ContextOSNavigator3D({ nodes, edges, selectedId, onSelect }: ContextOSNavigator3DProps): ReactElement {
  const visible = nodes.slice(0, MAX_NODES);
  const positions = useMemo(() => {
    const map = new Map<string, Point3>();
    const rows = new Map<number, number>();
    for (const node of visible) {
      const row = rows.get(node.layer) ?? 0;
      rows.set(node.layer, row + 1);
      map.set(node.id, {
        x: node.layer * LAYER_SPACING,
        y: -row * ROW_SPACING,
        z: (hashOf(node.id) % 7) * 3 - 9,
      });
    }
    return map;
  }, [visible]);

  return (
    <div className="relative h-full w-full" data-testid="contextos-nav-3d">
      <Canvas camera={{ position: [10, 12, 70], fov: 55 }}>
        <ambientLight intensity={0.6} />
        <pointLight position={[30, 30, 30]} intensity={1.1} />
        <group>
          {visible.map((node) => {
            const position = positions.get(node.id);
            return position ? (
              <NodePoint
                key={node.id}
                node={node}
                position={position}
                selected={selectedId === node.id}
                onSelect={onSelect}
              />
            ) : null;
          })}
          {edges.map((edge) => {
            const source = positions.get(edge.source);
            const target = positions.get(edge.target);
            if (!source || !target) return null;
            const animated = edge.animated;
            return (
              <Line
                key={edge.id}
                points={[[source.x, source.y, source.z], [target.x, target.y, target.z]]}
                color={animated ? '#ec835a' : '#383835'}
                lineWidth={animated ? 1.6 : 1}
                transparent
                opacity={animated ? 0.85 : 0.55}
              />
            );
          })}
        </group>
        <OrbitControls enablePan enableZoom enableRotate autoRotate autoRotateSpeed={0.4} />
        <gridHelper args={[220, 44, 0x2c2c2a, 0x1a1a19]} position={[60, -70, 0]} />
      </Canvas>
      <p className="pointer-events-none absolute bottom-1 left-2 text-[8px] text-[#898781]">
        3D 探索视图{nodes.length > MAX_NODES ? ` · 仅渲染前 ${MAX_NODES} 个节点` : ''} · 拖拽旋转 / 滚轮缩放 · 点击节点查看状态
      </p>
    </div>
  );
}
