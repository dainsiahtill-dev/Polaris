import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// @ts-nocheck
import { useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Stars, Html } from '@react-three/drei';
const CLUSTER_COLORS = [
    '#8b9e96',
    '#b0a094',
    '#a4c2b6',
    '#c9b99a',
    '#9a8f85',
    '#7a8f9e',
    '#d4cec4', // Warm white
];
function DataPoints({ points }) {
    // Use InstancedMesh for performance if many points, but for < 1000 points, simple mapping is fine for now and easier to make interactive
    // Actually, let's use a simple mapping of spheres first to ensure it works.
    const [hovered, setHovered] = useState(null);
    return (_jsxs("group", { children: [points.map((p, i) => (_jsxs("mesh", { position: [p.x, p.y, p.z], onPointerOver: (e) => { e.stopPropagation(); setHovered(p.path); }, onPointerOut: () => setHovered(null), children: [_jsx("sphereGeometry", { args: [0.3, 16, 16] }), _jsx("meshStandardMaterial", { color: CLUSTER_COLORS[p.cluster % CLUSTER_COLORS.length], emissive: CLUSTER_COLORS[p.cluster % CLUSTER_COLORS.length], emissiveIntensity: hovered === p.path ? 0.8 : 0.2 }), hovered === p.path && (_jsx(Html, { distanceFactor: 10, children: _jsx("div", { className: "soft-raised p-2 rounded-lg text-xs whitespace-nowrap", children: p.path }) }))] }, p.path))), _jsx(Stars, { radius: 100, depth: 50, count: 5000, factor: 4, saturation: 0, fade: true, speed: 1 })] }));
}
export function CodeMap3D({ points }) {
    return (_jsxs("div", { className: "w-full h-[500px] rounded-lg overflow-hidden soft-inset relative", children: [_jsxs(Canvas, { camera: { position: [0, 0, 20], fov: 60 }, children: [_jsx("ambientLight", { intensity: 0.5 }), _jsx("pointLight", { position: [10, 10, 10], intensity: 1 }), _jsx("group", { children: _jsx(DataPoints, { points: points }) }), _jsx(OrbitControls, { enablePan: true, enableZoom: true, enableRotate: true, autoRotate: true, autoRotateSpeed: 0.5 }), _jsx("gridHelper", { args: [50, 50, 0x333333, 0x111111], position: [0, -10, 0] })] }), _jsxs("div", { className: "absolute top-2 left-2 text-xs text-white/50 pointer-events-none", children: ["Wait for connection...", _jsx("br", {}), "Left Click: Rotate | Right Click: Pan | Scroll: Zoom"] })] }));
}
