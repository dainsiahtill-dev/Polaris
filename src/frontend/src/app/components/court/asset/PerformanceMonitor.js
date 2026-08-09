import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * 性能监控与LOD管理系统
 *
 * 实现：
 * - FPS 监控
 * - 自适应 LOD 降级
 * - Draw call 统计
 * - 内存使用追踪
 */
import { useRef, useState, useEffect } from 'react';
import { devLogger } from '@/app/utils/devLogger';
// LOD 级别配置
export const LOD_LEVELS = {
    HIGH: 0,
    MEDIUM: 1,
    LOW: 2,
    MINIMAL: 3, // 仅 billboard
};
// 性能阈值配置
const PERFORMANCE_THRESHOLDS = {
    fps: {
        excellent: 55,
        good: 40,
        poor: 30,
        critical: 20,
    },
    memory: {
        warning: 0.7,
        critical: 0.85, // 85% 内存使用
    },
};
/**
 * React Hook: 性能监控
 */
export function usePerformanceMonitor(targetFPS = 30) {
    const [metrics, setMetrics] = useState({
        fps: 60,
        frameTime: 16.67,
        drawCalls: 0,
        triangles: 0,
        geometries: 0,
        textures: 0,
        memory: { used: 0, total: 0, limit: 0 },
    });
    const [lodSettings, setLodSettings] = useState({
        currentLevel: LOD_LEVELS.HIGH,
        targetDistance: 50,
        maxActors: 24,
        enableAnimations: true,
        shadowQuality: 'high',
    });
    const [adaptiveLOD, setAdaptiveLOD] = useState(true);
    // FPS 计算
    const frameCountRef = useRef(0);
    const lastTimeRef = useRef(performance.now());
    const fpsHistoryRef = useRef([]);
    useEffect(() => {
        let animationId;
        const measure = () => {
            const now = performance.now();
            const delta = now - lastTimeRef.current;
            frameCountRef.current++;
            if (delta >= 1000) {
                const fps = Math.round((frameCountRef.current * 1000) / delta);
                frameCountRef.current = 0;
                lastTimeRef.current = now;
                // 保存 FPS 历史
                fpsHistoryRef.current.push(fps);
                if (fpsHistoryRef.current.length > 60) {
                    fpsHistoryRef.current.shift();
                }
                // 计算平均 FPS
                const avgFPS = Math.round(fpsHistoryRef.current.reduce((a, b) => a + b, 0) / fpsHistoryRef.current.length);
                setMetrics((prev) => ({
                    ...prev,
                    fps: avgFPS,
                    frameTime: 1000 / Math.max(fps, 1),
                }));
            }
            animationId = requestAnimationFrame(measure);
        };
        animationId = requestAnimationFrame(measure);
        return () => cancelAnimationFrame(animationId);
    }, []);
    // 自适应 LOD 调整
    useEffect(() => {
        if (!adaptiveLOD)
            return;
        const avgFPS = metrics.fps;
        const newSettings = { ...lodSettings };
        let changed = false;
        // 根据 FPS 调整 LOD
        if (avgFPS < PERFORMANCE_THRESHOLDS.fps.critical) {
            // 严重掉帧，大幅降级
            if (newSettings.currentLevel < LOD_LEVELS.MINIMAL) {
                newSettings.currentLevel++;
                newSettings.shadowQuality = 'off';
                newSettings.enableAnimations = false;
                changed = true;
            }
        }
        else if (avgFPS < PERFORMANCE_THRESHOLDS.fps.poor) {
            // 较差，降级
            if (newSettings.currentLevel < LOD_LEVELS.LOW) {
                newSettings.currentLevel++;
                newSettings.shadowQuality = 'low';
                changed = true;
            }
        }
        else if (avgFPS > PERFORMANCE_THRESHOLDS.fps.excellent && newSettings.currentLevel > LOD_LEVELS.HIGH) {
            // 性能优秀，可以升级
            newSettings.currentLevel--;
            newSettings.enableAnimations = true;
            if (newSettings.currentLevel <= LOD_LEVELS.MEDIUM) {
                newSettings.shadowQuality = 'medium';
            }
            changed = true;
        }
        if (changed) {
            setLodSettings(newSettings);
            devLogger.debug(`[Performance] LOD adjusted to level ${newSettings.currentLevel}, FPS: ${avgFPS}`);
        }
    }, [metrics.fps, adaptiveLOD, lodSettings]);
    return {
        metrics,
        lodSettings,
        adaptiveLOD,
        setAdaptiveLOD,
    };
}
/**
 * 计算角色应使用的 LOD 级别
 */
export function calculateLODLevel(distance, baseLevel, performanceLevel) {
    // 距离 LOD
    let distanceLOD = LOD_LEVELS.HIGH;
    if (distance > 30)
        distanceLOD = LOD_LEVELS.LOW;
    else if (distance > 15)
        distanceLOD = LOD_LEVELS.MEDIUM;
    // 性能 LOD
    const performanceLOD = Math.min(performanceLevel, LOD_LEVELS.MINIMAL);
    // 取最高（最差）级别
    return Math.max(distanceLOD, performanceLOD, baseLevel);
}
/**
 * 判断是否应渲染角色（视锥剔除）
 */
export function shouldRenderActor(position, cameraPosition, cameraDirection, fov = 60) {
    // 计算到相机的距离
    const dx = position[0] - cameraPosition[0];
    const dy = position[1] - cameraPosition[1];
    const dz = position[2] - cameraPosition[2];
    const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
    // 如果太近或太远，不渲染
    if (distance < 0.5 || distance > 100) {
        return false;
    }
    // 计算是否在视锥内
    const dot = (dx / distance) * cameraDirection[0] +
        (dy / distance) * cameraDirection[1] +
        (dz / distance) * cameraDirection[2];
    const cosHalfFov = Math.cos((fov * Math.PI) / 360);
    return dot > cosHalfFov * 0.5; // 0.5 为宽松系数
}
/**
 * React Hook: 可见性管理
 */
export function useVisibilityCulling(actorPositions, cameraPosition, cameraDirection) {
    const [visibleActors, setVisibleActors] = useState(new Set());
    useEffect(() => {
        const visible = new Set();
        actorPositions.forEach((position, roleId) => {
            if (shouldRenderActor(position, cameraPosition, cameraDirection)) {
                visible.add(roleId);
            }
        });
        setVisibleActors(visible);
    }, [actorPositions, cameraPosition, cameraDirection]);
    return visibleActors;
}
/**
 * 性能面板组件
 */
export function PerformancePanel({ metrics, lodSettings, onToggleAdaptive, }) {
    const fpsColor = metrics.fps >= 55 ? '#00ff00' :
        metrics.fps >= 40 ? '#88ff00' :
            metrics.fps >= 30 ? '#ffff00' :
                '#ff0000';
    return (_jsxs("div", { className: "absolute top-4 right-4 bg-black/80 border border-slate-700 rounded-lg p-3 text-xs font-mono z-50", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsx("span", { className: "text-slate-400", children: "Performance" }), _jsx("button", { onClick: onToggleAdaptive, className: "px-2 py-0.5 bg-slate-700 rounded text-slate-300 hover:bg-slate-600", children: "Auto LOD" })] }), _jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-500", children: "FPS:" }), _jsx("span", { style: { color: fpsColor }, children: metrics.fps })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-500", children: "Frame Time:" }), _jsxs("span", { className: "text-slate-300", children: [metrics.frameTime.toFixed(1), "ms"] })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-500", children: "LOD Level:" }), _jsx("span", { className: "text-amber-400", children: lodSettings.currentLevel })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-500", children: "Shadows:" }), _jsx("span", { className: "text-slate-300", children: lodSettings.shadowQuality })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-slate-500", children: "Animations:" }), _jsx("span", { className: lodSettings.enableAnimations ? 'text-green-400' : 'text-red-400', children: lodSettings.enableAnimations ? 'ON' : 'OFF' })] })] })] }));
}
