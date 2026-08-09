import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
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
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Stars, Grid, PerspectiveCamera } from '@react-three/drei';
import * as THREE from 'three';
import { CourtActor3D } from './CourtActor3D';
import { SCENE_NAMES, STATUS_COLORS, RISK_COLORS } from '../../types/court';
import { usePerformanceMonitor, PerformancePanel } from './asset';
import { devLogger } from '@/app/utils/devLogger';
// 镜头控制器组件
function CameraController({ mode, targetRoleId, topology, courtState, }) {
    const { camera } = useThree();
    const targetRef = useRef(new THREE.Vector3(0, 0, 0));
    const positionRef = useRef(new THREE.Vector3(0, 8, 15));
    // 根据模式和目标计算相机位置和焦点
    useEffect(() => {
        const sceneConfig = courtState?.current_scene
            ? courtState.current_scene
            : 'taiji_hall';
        switch (mode) {
            case 'overview':
                // 总览模式：根据当前场景调整
                if (sceneConfig === 'taiji_hall') {
                    positionRef.current.set(0, 10, 18);
                    targetRef.current.set(0, 0, 0);
                }
                else if (sceneConfig === 'zhongshu_pavilion') {
                    positionRef.current.set(-6, 6, 12);
                    targetRef.current.set(-4, 0, 2);
                }
                else if (sceneConfig === 'gongbu_blueprint') {
                    positionRef.current.set(6, 5, 10);
                    targetRef.current.set(6, 0, 6);
                }
                else {
                    positionRef.current.set(0, 8, 15);
                    targetRef.current.set(0, 0, 0);
                }
                break;
            case 'focus':
                // 聚焦模式：聚焦到特定角色所在区域
                if (targetRoleId) {
                    const node = topology.find((n) => n.role_id === targetRoleId);
                    if (node) {
                        positionRef.current.set(node.position[0] + 3, node.position[1] + 4, node.position[2] + 6);
                        targetRef.current.set(...node.position);
                    }
                }
                break;
            case 'inspect':
                // 检查模式：近距离观察单个角色
                if (targetRoleId) {
                    const node = topology.find((n) => n.role_id === targetRoleId);
                    if (node) {
                        positionRef.current.set(node.position[0] + 1.5, node.position[1] + 2, node.position[2] + 3);
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
function ConnectionLines({ topology }) {
    const lines = useMemo(() => {
        const result = [];
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
    return (_jsx("group", { children: lines.map((line, index) => (_jsxs("line", { children: [_jsx("bufferGeometry", { children: _jsx("bufferAttribute", { attach: "attributes-position", count: 2, array: new Float32Array([
                            line.start.x, line.start.y, line.start.z,
                            line.end.x, line.end.y, line.end.z,
                        ]), itemSize: 3 }) }), _jsx("lineBasicMaterial", { color: line.color, transparent: true, opacity: 0.3 })] }, index))) }));
}
// 场景环境组件
function SceneEnvironment({ shadowQuality }) {
    const shadowMapSize = useMemo(() => {
        switch (shadowQuality) {
            case 'high': return [2048, 2048];
            case 'medium': return [1024, 1024];
            case 'low': return [512, 512];
            default: return undefined;
        }
    }, [shadowQuality]);
    return (_jsxs(_Fragment, { children: [_jsx("ambientLight", { intensity: 0.3, color: "#404060" }), _jsx("directionalLight", { position: [10, 20, 10], intensity: 1, color: "#fff8e7", castShadow: shadowQuality !== 'off', "shadow-mapSize": shadowMapSize }), _jsx("pointLight", { position: [-10, 10, -10], intensity: 0.5, color: "#4488ff" }), _jsx("pointLight", { position: [10, 5, -10], intensity: 0.3, color: "#ff8844" }), _jsx(Stars, { radius: 100, depth: 50, count: 3000, factor: 4, saturation: 0.5, fade: true, speed: 1 }), _jsx(Grid, { position: [0, -0.1, 0], args: [50, 50], cellSize: 1, cellThickness: 0.5, cellColor: "#334455", sectionSize: 5, sectionThickness: 1, sectionColor: "#445566", fadeDistance: 30, fadeStrength: 1, infiniteGrid: true })] }));
}
export function CourtScene({ courtState, topology, selectedRoleId, onSelectRole, cameraMode = 'overview', targetRoleId, enablePerformanceMonitor = false, usePlaceholderAssets = true, }) {
    const [localSelected, setLocalSelected] = useState(null);
    // 合并外部和内部选择状态
    const effectiveSelectedId = selectedRoleId ?? localSelected;
    // 性能监控
    const { metrics, lodSettings, adaptiveLOD, setAdaptiveLOD } = usePerformanceMonitor(30);
    const handleActorClick = useCallback((roleId) => {
        const newSelection = effectiveSelectedId === roleId ? null : roleId;
        setLocalSelected(newSelection);
        onSelectRole?.(newSelection);
    }, [effectiveSelectedId, onSelectRole]);
    // 构建角色状态映射
    const actorMap = useMemo(() => {
        return courtState?.actors ?? {};
    }, [courtState]);
    return (_jsxs("div", { className: "w-full h-full relative bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950", children: [_jsxs("div", { className: "absolute top-4 left-4 z-10 pointer-events-none", children: [_jsx("h2", { className: "text-2xl font-bold text-amber-100/90 drop-shadow-lg", children: courtState?.current_scene ? SCENE_NAMES[courtState.current_scene] ?? '宫廷' : '宫廷' }), _jsx("p", { className: "text-sm text-amber-200/60 mt-1", children: courtState?.phase ?? 'court_audience' })] }), enablePerformanceMonitor && (_jsx(PerformancePanel, { metrics: metrics, lodSettings: lodSettings, onToggleAdaptive: () => setAdaptiveLOD(!adaptiveLOD) })), _jsxs(Canvas, { shadows: lodSettings.shadowQuality !== 'off', children: [_jsx(PerspectiveCamera, { makeDefault: true, fov: 60, near: 0.1, far: 1000 }), _jsx(CameraController, { mode: cameraMode, targetRoleId: targetRoleId ?? effectiveSelectedId, topology: topology, courtState: courtState }), _jsx(SceneEnvironment, { shadowQuality: lodSettings.shadowQuality }), _jsx(ConnectionLines, { topology: topology }), _jsx("group", { children: topology.map((node) => (_jsx(CourtActor3D, { node: node, actor: actorMap[node.role_id], isSelected: effectiveSelectedId === node.role_id, onClick: () => handleActorClick(node.role_id), lodSettings: lodSettings, usePlaceholder: usePlaceholderAssets }, node.role_id))) }), _jsx(OrbitControls, { enablePan: cameraMode !== 'inspect', enableZoom: true, enableRotate: cameraMode !== 'inspect', minDistance: 2, maxDistance: 50, maxPolarAngle: Math.PI / 2 - 0.1 })] }), _jsx("div", { className: "absolute bottom-4 left-4 text-xs text-white/40 pointer-events-none", children: "\u5DE6\u952E\u70B9\u51FB: \u9009\u62E9\u89D2\u8272 | \u5DE6\u952E\u62D6\u62FD: \u65CB\u8F6C\u89C6\u89D2 | \u6EDA\u8F6E: \u7F29\u653E | \u53F3\u952E\u62D6\u62FD: \u5E73\u79FB" }), effectiveSelectedId && actorMap[effectiveSelectedId] && (_jsx(ActorInfoPanel, { actor: actorMap[effectiveSelectedId], onClose: () => {
                    setLocalSelected(null);
                    onSelectRole?.(null);
                } }))] }));
}
// 角色信息面板
function ActorInfoPanel({ actor, onClose, }) {
    return (_jsx("div", { className: "absolute top-4 right-4 w-72 bg-slate-900/95 border border-slate-700 rounded-lg shadow-2xl backdrop-blur-md z-20", children: _jsxs("div", { className: "p-4", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsx("h3", { className: "text-lg font-bold text-amber-100", children: actor.role_name }), _jsx("button", { onClick: onClose, className: "text-slate-400 hover:text-white transition-colors", children: "\u2715" })] }), _jsxs("div", { className: "space-y-2 text-sm", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-400", children: "\u72B6\u6001:" }), _jsx("span", { className: "font-medium", style: { color: STATUS_COLORS[actor.status] }, children: actor.status })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-400", children: "\u5F53\u524D\u52A8\u4F5C:" }), _jsx("span", { className: "text-slate-200", children: actor.current_action || '-' })] }), actor.task_id && (_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-400", children: "\u4EFB\u52A1ID:" }), _jsx("span", { className: "text-slate-300 font-mono text-xs", children: actor.task_id })] })), actor.risk_level !== 'none' && (_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-400", children: "\u98CE\u9669\u7B49\u7EA7:" }), _jsx("span", { className: "font-medium", style: { color: RISK_COLORS[actor.risk_level] }, children: actor.risk_level })] })), actor.evidence_refs.length > 0 && (_jsxs("div", { className: "mt-3", children: [_jsx("span", { className: "text-slate-400 block mb-2", children: "\u8BC1\u636E\u94FE:" }), _jsx("div", { className: "space-y-1 max-h-32 overflow-y-auto", children: actor.evidence_refs.map((ref, index) => (_jsx("button", { className: "block w-full text-left px-2 py-1 bg-slate-800/50 rounded text-xs text-cyan-300 hover:bg-slate-700/50 transition-colors truncate", onClick: () => {
                                            // TODO: 跳转到证据详情
                                            devLogger.debug('Navigate to evidence:', ref);
                                        }, children: ref.path }, index))) })] }))] })] }) }));
}
export default CourtScene;
