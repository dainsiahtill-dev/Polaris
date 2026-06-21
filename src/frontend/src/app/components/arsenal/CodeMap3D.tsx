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
  '#8b9e96', // Sage (soft-accent family)
  '#b0a094', // Warm sand
  '#a4c2b6', // Jade mist
  '#c9b99a', // Amber mist
  '#9a8f85', // Stone
  '#7a8f9e', // Slate blue
  '#d4cec4', // Warm white
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
            emissiveIntensity={hovered === p.path ? 0.8 : 0.2}
          />
          {hovered === p.path && (
            <Html distanceFactor={10}>
              <div className="soft-raised p-2 rounded-lg text-xs whitespace-nowrap">
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
    <div className="w-full h-[500px] rounded-lg overflow-hidden soft-inset relative">
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
