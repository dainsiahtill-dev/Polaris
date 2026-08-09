import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '@/app/components/ui/tooltip';
import { devLogger } from '@/app/utils/devLogger';
import { Link2, Link2Off, Download, Shield, Activity, } from 'lucide-react';
import { detachRoleSession, exportRoleSessionSnapshot, getRoleCapabilities, resolveRoleCapabilities, } from '@/services/roleSessionService';
/**
 * Session Inspector - 会话侧边栏组件
 *
 * 显示当前会话的详细信息：
 * - Session ID
 * - Host Kind / Attachment Mode
 * - Capabilities
 * - 工具调用统计
 * - 快速操作（attach/detach/export）
 */
export function SessionInspector({ sessionId, role, hostKind = 'electron_workbench', attachmentMode = 'isolated', attachedRunId, attachedTaskId, workspace, onAttach, onDetach, onExport, }) {
    const [capabilities, setCapabilities] = useState([]);
    const [loading, setLoading] = useState(true);
    // 加载能力配置
    useEffect(() => {
        const loadCapabilities = async () => {
            try {
                const result = await getRoleCapabilities(role, hostKind);
                if (result.ok) {
                    setCapabilities(resolveRoleCapabilities(result.data, hostKind));
                }
                else {
                    devLogger.error('[SessionInspector] Failed to load capabilities:', result.error);
                }
            }
            catch (err) {
                devLogger.error('[SessionInspector] Failed to load capabilities:', err);
            }
            finally {
                setLoading(false);
            }
        };
        loadCapabilities();
    }, [role, hostKind]);
    const getHostKindLabel = (kind) => {
        const labels = {
            workflow: '工作流',
            electron_workbench: '工作台',
            tui: 'TUI',
            cli: 'CLI',
            api_server: 'API',
            headless: '无头',
        };
        return labels[kind] || kind;
    };
    const getAttachmentModeLabel = (mode) => {
        const labels = {
            isolated: '隔离',
            attached_readonly: '只读',
            attached_collaborative: '协作',
        };
        return labels[mode] || mode;
    };
    const handleAttach = async () => {
        onAttach?.();
    };
    const handleDetach = async () => {
        if (!sessionId)
            return;
        try {
            const result = await detachRoleSession(sessionId);
            if (result.ok) {
                onDetach?.();
            }
            else {
                devLogger.error('[SessionInspector] Failed to detach:', result.error);
            }
        }
        catch (err) {
            devLogger.error('[SessionInspector] Failed to detach:', err);
        }
    };
    const handleExport = async () => {
        if (!sessionId)
            return;
        try {
            const result = await exportRoleSessionSnapshot(sessionId, {
                include_messages: true,
                format: 'json',
            });
            if (result.ok) {
                onExport?.();
            }
            else {
                devLogger.error('[SessionInspector] Failed to export:', result.error);
            }
        }
        catch (err) {
            devLogger.error('[SessionInspector] Failed to export:', err);
        }
    };
    return (_jsxs(Card, { className: "w-full bg-slate-900 border-slate-700", children: [_jsxs(CardHeader, { className: "pb-3", children: [_jsxs(CardTitle, { className: "text-sm font-medium text-slate-200 flex items-center gap-2", children: [_jsx(Activity, { className: "w-4 h-4" }), "\u4F1A\u8BDD\u72B6\u6001"] }), _jsxs(CardDescription, { className: "text-xs text-slate-400", children: ["Session: ", sessionId.slice(0, 8), "..."] })] }), _jsxs(CardContent, { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-slate-400", children: "\u5BBF\u4E3B\u7C7B\u578B" }), _jsx(Badge, { variant: "outline", className: "text-xs", children: getHostKindLabel(hostKind) })] }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-slate-400", children: "\u9644\u7740\u6A21\u5F0F" }), _jsx(Badge, { variant: "outline", className: `text-xs ${attachmentMode === 'isolated'
                                    ? 'border-yellow-500 text-yellow-500'
                                    : 'border-green-500 text-green-500'}`, children: getAttachmentModeLabel(attachmentMode) })] }), (attachedRunId || attachedTaskId) && (_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-slate-400", children: "\u5DF2\u9644\u7740\u5230" }), _jsxs("span", { className: "text-xs text-slate-300", children: [attachedRunId && `Run: ${attachedRunId.slice(0, 6)}`, attachedTaskId && ` Task: ${attachedTaskId.slice(0, 6)}`] })] })), _jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex items-center gap-1 text-xs text-slate-400", children: [_jsx(Shield, { className: "w-3 h-3" }), "\u80FD\u529B"] }), _jsxs("div", { className: "flex flex-wrap gap-1", children: [loading ? (_jsx("span", { className: "text-xs text-slate-500", children: "\u52A0\u8F7D\u4E2D..." })) : (capabilities.slice(0, 4).map((cap) => (_jsx(Badge, { variant: "secondary", className: "text-[10px] px-1 py-0", children: cap.replace(/_/g, ' ') }, cap)))), capabilities.length > 4 && (_jsxs(Badge, { variant: "secondary", className: "text-[10px] px-1 py-0", children: ["+", capabilities.length - 4] }))] })] }), _jsxs("div", { className: "pt-2 border-t border-slate-700 space-y-2", children: [attachmentMode === 'isolated' ? (_jsx(TooltipProvider, { children: _jsxs(Tooltip, { children: [_jsx(TooltipTrigger, { asChild: true, children: _jsxs(Button, { variant: "outline", size: "sm", className: "w-full text-xs", onClick: handleAttach, children: [_jsx(Link2, { className: "w-3 h-3 mr-1" }), "\u9644\u7740\u5230\u5DE5\u4F5C\u6D41"] }) }), _jsx(TooltipContent, { children: _jsx("p", { className: "text-xs", children: "\u5C06\u4F1A\u8BDD\u9644\u7740\u5230\u5F53\u524D\u5DE5\u4F5C\u6D41" }) })] }) })) : (_jsx(TooltipProvider, { children: _jsxs(Tooltip, { children: [_jsx(TooltipTrigger, { asChild: true, children: _jsxs(Button, { variant: "outline", size: "sm", className: "w-full text-xs", onClick: handleDetach, children: [_jsx(Link2Off, { className: "w-3 h-3 mr-1" }), "\u89E3\u9664\u9644\u7740"] }) }), _jsx(TooltipContent, { children: _jsx("p", { className: "text-xs", children: "\u5C06\u4F1A\u8BDD\u4ECE\u5DE5\u4F5C\u6D41\u5206\u79BB" }) })] }) })), _jsx(TooltipProvider, { children: _jsxs(Tooltip, { children: [_jsx(TooltipTrigger, { asChild: true, children: _jsxs(Button, { variant: "outline", size: "sm", className: "w-full text-xs", onClick: handleExport, children: [_jsx(Download, { className: "w-3 h-3 mr-1" }), "\u5BFC\u51FA\u4F1A\u8BDD"] }) }), _jsx(TooltipContent, { children: _jsx("p", { className: "text-xs", children: "\u5BFC\u51FA\u4E3A JSON \u6216 Markdown" }) })] }) })] })] })] }));
}
