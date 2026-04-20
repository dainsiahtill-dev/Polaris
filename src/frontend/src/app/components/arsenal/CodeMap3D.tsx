// @ts-nocheck
import React, { useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, ThreeEvent } from '@react-three/fiber';
import { OrbitControls, Text, Stars, Html } from '@react-three/drei';
import * as THREE from 'three';

interface Point {
  path: string;
  x: number;
  y: number;
  z: number;
  cluster: number;
}

interface CodeMapProps {
  points: Point[];
}

const CLUSTER_COLORS = [
  '#00ffff', // Cyan (Cyberpunk main)
  '#ff00ff', // Magenta
  '#ffff00', // Yellow
  '#00ff00', // Green
  '#ff0000', // Red
  '#0000ff', // Blue
  '#ffffff', // White
];

function DataPoints({ points }: { points: Point[] }) {
  // Use InstancedMesh for performance if many points, but for < 1000 points, simple mapping is fine for now and easier to make interactive
  // Actually, let's use a simple mapping of spheres first to ensure it works.
  
  const [hovered, setHovered] = useState<string | null>(null);

  return (
    <group>
      {points.map((p, i) => (
        <mesh
          key={p.path}
          position={[p.x, p.y, p.z]}
          onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); setHovered(p.path); }}
          onPointerOut={() => setHovered(null)}
        >
          <sphereGeometry args={[0.3, 16, 16]} />
          <meshStandardMaterial 
            color={CLUSTER_COLORS[p.cluster % CLUSTER_COLORS.length]} 
            emissive={CLUSTER_COLORS[p.cluster % CLUSTER_COLORS.length]}
            emissiveIntensity={hovered === p.path ? 2 : 0.5}
          />
          {hovered === p.path && (
            <Html distanceFactor={10}>
              <div className="bg-black/80 text-cyan-400 p-2 rounded border border-cyan-500/50 text-xs whitespace-nowrap backdrop-blur-md">
                {p.path}
              </div>
            </Html>
          )}
        </mesh>
      ))}
      <Stars radius={100} depth={50} count={5000} factor={4} saturation={0} fade speed={1} />
    </group>
  );
}

export function CodeMap3D({ points }: CodeMapProps) {
  return (
    <div className="w-full h-[500px] rounded-lg overflow-hidden border border-white/10 bg-black/90 relative">
      <Canvas camera={{ position: [0, 0, 20], fov: 60 }}>
        <ambientLight intensity={0.5} />
        <pointLight position={[10, 10, 10]} intensity={1} />
        <group>
           <DataPoints points={points} />
        </group>
        <OrbitControls 
            enablePan={true} 
            enableZoom={true} 
            enableRotate={true} 
            autoRotate={true} 
            autoRotateSpeed={0.5} 
        />
        <gridHelper args={[50, 50, 0x333333, 0x111111]} position={[0, -10, 0]} />
      </Canvas>
      <div className="absolute top-2 left-2 text-xs text-white/50 pointer-events-none">
        Wait for connection...
        <br/>
        Left Click: Rotate | Right Click: Pan | Scroll: Zoom
      </div>
    </div>
  );
}

export {};
