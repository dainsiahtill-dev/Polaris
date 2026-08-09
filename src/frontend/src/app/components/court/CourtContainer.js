import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * 宫廷投影容器组件
 *
 * 整合数据获取、WebSocket 实时更新和场景渲染
 */
import { useState, useMemo, useCallback } from 'react';
import { CourtScene } from './CourtScene';
import { useCourtTopology, useCourtState } from '../../hooks/useCourt';
export function CourtContainer({ defaultCameraMode = 'overview', onActorSelect, enableRealtime = true, }) {
    // 获取拓扑结构（静态，只获取一次）
    const { topology: topologyData, loading: topologyLoading } = useCourtTopology();
    // 获取初始状态；之后由 RuntimeTransportProvider 实时更新。
    const { state: courtState, isWebSocketConnected } = useCourtState({ enabled: enableRealtime });
    // 相机模式和选中的角色
    const [cameraMode, setCameraMode] = useState(defaultCameraMode);
    const [selectedRoleId, setSelectedRoleId] = useState(null);
    // 拓扑节点列表
    const topology = useMemo(() => {
        return topologyData?.nodes ?? [];
    }, [topologyData]);
    // 角色选择处理
    const handleSelectRole = useCallback((roleId) => {
        setSelectedRoleId(roleId);
        if (onActorSelect) {
            const actor = roleId ? courtState?.actors?.[roleId] ?? null : null;
            onActorSelect(actor);
        }
        // 选择角色时自动切换到 inspect 模式
        if (roleId && cameraMode === 'overview') {
            setCameraMode('focus');
        }
    }, [courtState, onActorSelect, cameraMode]);
    // 切换相机模式
    const handleCameraModeChange = useCallback((mode) => {
        setCameraMode(mode);
        if (mode === 'overview') {
            setSelectedRoleId(null);
            onActorSelect?.(null);
        }
    }, [onActorSelect]);
    if (topologyLoading) {
        return (_jsx("div", { className: "w-full h-full flex items-center justify-center bg-slate-950", children: _jsxs("div", { className: "text-center", children: [_jsx("div", { className: "w-12 h-12 border-4 border-amber-500/30 border-t-amber-500 rounded-full animate-spin mx-auto mb-4" }), _jsx("p", { className: "text-amber-200/60", children: "\u52A0\u8F7D\u5BAB\u5EF7\u573A\u666F...\u26E9\uFE0F" })] }) }));
    }
    if (!topology.length) {
        return (_jsx("div", { className: "w-full h-full flex items-center justify-center bg-slate-950", children: _jsxs("div", { className: "text-center text-red-400", children: [_jsx("p", { children: "\u52A0\u8F7D\u5BAB\u5EF7\u62D3\u6251\u5931\u8D25" }), _jsx("p", { className: "text-sm text-red-400/60 mt-2", children: "\u8BF7\u68C0\u67E5\u7F51\u7EDC\u8FDE\u63A5\u5E76\u5237\u65B0\u9875\u9762" })] }) }));
    }
    return (_jsxs("div", { className: "w-full h-full flex flex-col", children: [_jsxs("div", { className: "h-12 bg-slate-900/80 border-b border-slate-700 flex items-center px-4 justify-between", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx("span", { className: "text-amber-100 font-medium", children: "\u5BAB\u5EF7\u6295\u5F71" }), _jsxs("div", { className: "flex items-center gap-2 text-xs", children: [_jsx("div", { className: `w-2 h-2 rounded-full ${isWebSocketConnected ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'}` }), _jsx("span", { className: isWebSocketConnected ? 'text-green-400' : 'text-yellow-400', children: isWebSocketConnected ? '实时' : '等待实时' })] })] }), _jsx("div", { className: "flex items-center gap-2", children: ['overview', 'focus', 'inspect'].map((mode) => (_jsxs("button", { onClick: () => handleCameraModeChange(mode), className: `px-3 py-1 text-xs rounded transition-colors ${cameraMode === mode
                                ? 'bg-amber-600 text-white'
                                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`, children: [mode === 'overview' && '总览', mode === 'focus' && '聚焦', mode === 'inspect' && '检查'] }, mode))) })] }), _jsx("div", { className: "flex-1", children: _jsx(CourtScene, { courtState: courtState, topology: topology, selectedRoleId: selectedRoleId, onSelectRole: handleSelectRole, cameraMode: cameraMode, targetRoleId: selectedRoleId }) })] }));
}
export default CourtContainer;
