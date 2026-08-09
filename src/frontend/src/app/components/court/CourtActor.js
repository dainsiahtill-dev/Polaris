import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * Court Role Entity Component
 *
 * Render a single court role in 3D scene，支持状态颜色、动画和交互
 */
import { useRef, useState, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html, Billboard } from '@react-three/drei';
import * as THREE from 'three';
import { STATUS_COLORS, RISK_COLORS } from '../../types/court';
// Role geometry cache
const geometryCache = new Map();
function getGeometry(department) {
    if (!geometryCache.has(department)) {
        let geometry;
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
    return geometryCache.get(department);
}
export function CourtActor({ node, actor, isSelected, onClick }) {
    const meshRef = useRef(null);
    const [hovered, setHovered] = useState(false);
    // 根据状态确定颜色
    const baseColor = useMemo(() => {
        if (!actor)
            return '#888888';
        return STATUS_COLORS[actor.status] || '#888888';
    }, [actor]);
    // 风险等级发光颜色
    const riskColor = useMemo(() => {
        if (!actor || actor.risk_level === 'none')
            return null;
        return RISK_COLORS[actor.risk_level];
    }, [actor]);
    // 动画效果
    useFrame((state) => {
        if (!meshRef.current)
            return;
        // 根据状态添加动画
        if (actor?.status === 'executing' || actor?.status === 'dispatching') {
            // 执行中：上下浮动
            meshRef.current.position.y = node.position[1] + Math.sin(state.clock.elapsedTime * 3) * 0.1;
        }
        else if (actor?.status === 'thinking') {
            // 思考中：轻微旋转
            meshRef.current.rotation.y = Math.sin(state.clock.elapsedTime) * 0.1;
        }
        else if (actor?.status === 'blocked' || actor?.status === 'failed') {
            // 阻塞/失败：红色脉冲
            const pulse = (Math.sin(state.clock.elapsedTime * 5) + 1) * 0.5;
            meshRef.current.scale.setScalar(1 + pulse * 0.1);
        }
        else {
            // 恢复默认位置和缩放
            meshRef.current.position.y = THREE.MathUtils.lerp(meshRef.current.position.y, node.position[1], 0.1);
            meshRef.current.scale.lerp(new THREE.Vector3(1, 1, 1), 0.1);
        }
    });
    const geometry = useMemo(() => getGeometry(node.department), [node.department]);
    return (_jsxs("group", { position: node.position, children: [isSelected && (_jsxs("mesh", { rotation: [-Math.PI / 2, 0, 0], position: [0, 0.05, 0], children: [_jsx("ringGeometry", { args: [0.8, 1, 32] }), _jsx("meshBasicMaterial", { color: "#a4c2b6", transparent: true, opacity: 0.8 })] })), _jsx("mesh", { ref: meshRef, geometry: geometry, onClick: (e) => {
                    e.stopPropagation();
                    onClick();
                }, onPointerOver: (e) => {
                    e.stopPropagation();
                    setHovered(true);
                }, onPointerOut: () => setHovered(false), children: _jsx("meshStandardMaterial", { color: baseColor, emissive: riskColor || baseColor, emissiveIntensity: hovered ? 0.8 : riskColor ? 0.5 : 0.2, metalness: 0.6, roughness: 0.4 }) }), _jsx(Billboard, { position: [0.6, 0.8, 0], children: _jsxs("mesh", { children: [_jsx("sphereGeometry", { args: [0.15, 8, 8] }), _jsx("meshBasicMaterial", { color: baseColor })] }) }), (hovered || isSelected) && (_jsx(Html, { distanceFactor: 10, position: [0, 1.5, 0], children: _jsxs("div", { className: `
              px-3 py-2 rounded-lg border backdrop-blur-md shadow-lg
              ${isSelected ? 'soft-raised' : 'soft-panel-subtle'}
              min-w-[120px]
            `, children: [_jsx("div", { className: "text-sm font-bold text-white", children: node.role_name }), actor && (_jsxs(_Fragment, { children: [_jsx("div", { className: "text-xs mt-1", style: { color: baseColor }, children: actor.current_action || actor.status }), actor.risk_level !== 'none' && (_jsxs("div", { className: "text-xs mt-1", style: { color: RISK_COLORS[actor.risk_level] }, children: ["Risk: ", actor.risk_level] }))] }))] }) }))] }));
}
export default CourtActor;
