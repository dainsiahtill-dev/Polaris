import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { AlertTriangle, CheckCircle, Clock, ShieldAlert, FileText, Settings, UserCog } from 'lucide-react';
import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, } from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { Badge } from '@/app/components/ui/badge';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { Tabs, TabsList, TabsTrigger } from '@/app/components/ui/tabs';
export function InterventionCenter({ isOpen, onClose }) {
    const [interventions, setInterventions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState('pending');
    const [selectedIntervention, setSelectedIntervention] = useState(null);
    const fetchInterventions = async () => {
        setLoading(true);
        try {
            const res = await apiFetch('/interventions/list');
            if (res.ok) {
                const data = await res.json();
                setInterventions(Array.isArray(data.interventions) ? data.interventions : []);
            }
        }
        catch (error) {
            devLogger.error('Failed to fetch interventions:', error);
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        if (isOpen) {
            fetchInterventions();
        }
    }, [isOpen]);
    const handleAction = async (interventionId, actionValue) => {
        try {
            const res = await apiFetch('/interventions/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: interventionId, action: actionValue }),
            });
            if (res.ok) {
                fetchInterventions();
                setSelectedIntervention(null);
            }
        }
        catch (error) {
            devLogger.error('Failed to perform action:', error);
        }
    };
    const getIcon = (type) => {
        switch (type) {
            case 'agent_confirmation':
                return _jsx(UserCog, { className: "h-4 w-4 text-blue-400" });
            case 'plan_change':
                return _jsx(FileText, { className: "h-4 w-4 text-amber-400" });
            case 'dependency_missing':
                return _jsx(AlertTriangle, { className: "h-4 w-4 text-red-400" });
            case 'policy_block':
                return _jsx(ShieldAlert, { className: "h-4 w-4 text-purple-400" });
            case 'manual_approval':
                return _jsx(CheckCircle, { className: "h-4 w-4 text-emerald-400" });
            default:
                return _jsx(Settings, { className: "h-4 w-4 text-gray-400" });
        }
    };
    const getStatusBadge = (status) => {
        switch (status) {
            case 'pending':
                return _jsx(Badge, { variant: "outline", className: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20", children: "\u5F85\u88C1" });
            case 'approved':
                return _jsx(Badge, { variant: "outline", className: "bg-green-500/10 text-green-500 border-green-500/20", children: "\u5DF2\u51C6" });
            case 'rejected':
                return _jsx(Badge, { variant: "outline", className: "bg-red-500/10 text-red-500 border-red-500/20", children: "\u9A73\u56DE" });
            case 'ignored':
                return _jsx(Badge, { variant: "outline", className: "bg-gray-500/10 text-gray-500 border-gray-500/20", children: "\u7565\u8FC7" });
            default:
                return null;
        }
    };
    const getStatusText = (status) => {
        switch (status) {
            case 'pending':
                return '待裁';
            case 'approved':
                return '已准';
            case 'rejected':
                return '驳回';
            case 'ignored':
                return '略过';
            default:
                return status;
        }
    };
    const getTypeLabel = (type) => {
        switch (type) {
            case 'agent_confirmation':
                return '角色请裁';
            case 'plan_change':
                return '方案变更';
            case 'dependency_missing':
                return '依赖缺失';
            case 'policy_block':
                return '门禁阻断';
            case 'manual_approval':
                return '人工核准';
            default:
                return type;
        }
    };
    const filteredInterventions = interventions.filter(i => {
        if (activeTab === 'pending')
            return i.status === 'pending';
        if (activeTab === 'history')
            return i.status !== 'pending';
        return true;
    });
    return (_jsx(Dialog, { open: isOpen, onOpenChange: (open) => {
            if (!open) {
                onClose();
            }
        }, children: _jsxs(DialogContent, { "data-testid": "intervention-center", className: "h-[min(80vh,46rem)] w-[min(64rem,calc(100vw-2rem))] max-w-none overflow-hidden flex flex-col bg-[var(--ink-indigo)] border-gray-800 text-gray-200 p-0 gap-0", style: { backgroundColor: 'rgb(18, 14, 42)' }, children: [_jsx(DialogTitle, { className: "sr-only", children: "Intervention Center" }), _jsx(DialogDescription, { className: "sr-only", children: "\u96C6\u4E2D\u5904\u7406\u4EBA\u5DE5\u6838\u51C6\u3001\u95E8\u7981\u963B\u65AD\u4E0E\u5173\u952E\u544A\u8B66\u3002" }), _jsx(DialogHeader, { className: "border-b border-gray-800 p-5 pr-16", children: _jsxs("div", { className: "grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-start", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("h2", { className: "flex min-w-0 items-center gap-2 text-xl font-semibold text-gray-100", children: [_jsx(ShieldAlert, { className: "h-5 w-5 shrink-0 text-emerald-500" }), _jsx("span", { className: "truncate", children: "Intervention Center" })] }), _jsx("p", { className: "mt-2 text-sm text-gray-400", children: "\u96C6\u4E2D\u5904\u7406\u4EBA\u5DE5\u6838\u51C6\u3001\u95E8\u7981\u963B\u65AD\u4E0E\u5173\u952E\u544A\u8B66\u3002" })] }), _jsx(Tabs, { value: activeTab, onValueChange: setActiveTab, className: "w-full md:w-[220px]", children: _jsxs(TabsList, { className: "grid w-full grid-cols-2 bg-gray-800", children: [_jsxs(TabsTrigger, { value: "pending", children: ["\u5F85\u88C1 (", interventions.filter(i => i.status === 'pending').length, ")"] }), _jsx(TabsTrigger, { value: "history", children: "\u5DF2\u51B3" })] }) })] }) }), _jsxs("div", { className: "flex-1 flex overflow-hidden", children: [_jsx("div", { className: "w-1/3 border-r border-gray-800 flex flex-col", children: _jsx(ScrollArea, { className: "flex-1", children: _jsx("div", { className: "p-4 space-y-2", children: loading ? (_jsx("div", { className: "text-center text-gray-500 py-8", children: "\u52A0\u8F7D\u4E2D..." })) : filteredInterventions.length === 0 ? (_jsx("div", { className: "text-center text-gray-500 py-8", children: "\u6682\u65E0\u4ECB\u5165\u4E8B\u9879\u3002" })) : (filteredInterventions.map((item) => (_jsxs("div", { onClick: () => setSelectedIntervention(item), className: `p-3 rounded-lg border cursor-pointer transition-colors ${selectedIntervention?.id === item.id
                                            ? 'bg-emerald-500/10 border-emerald-500/30'
                                            : 'bg-gray-800/30 border-gray-800 hover:bg-gray-800/50'}`, children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [getIcon(item.type), _jsx("span", { className: "text-xs font-medium text-gray-300 truncate max-w-[120px]", children: getTypeLabel(item.type) })] }), _jsx("span", { className: "text-[10px] text-gray-500", children: new Date(item.created_at).toLocaleTimeString() })] }), _jsx("div", { className: "font-medium text-sm text-gray-200 mb-1", children: item.title }), _jsx("div", { className: "flex items-center justify-between", children: getStatusBadge(item.status) })] }, item.id)))) }) }) }), _jsx("div", { className: "flex-1 flex flex-col bg-[#141414]", children: selectedIntervention ? (_jsxs(_Fragment, { children: [_jsx(ScrollArea, { className: "flex-1 p-6", children: _jsxs("div", { className: "space-y-6", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-lg font-semibold text-white mb-2", children: selectedIntervention.title }), _jsxs("div", { className: "flex items-center gap-3 text-sm text-gray-400", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Clock, { className: "h-3 w-3" }), new Date(selectedIntervention.created_at).toLocaleString()] }), _jsxs("span", { className: "px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-xs font-mono", children: ["ID: ", selectedIntervention.id.slice(0, 8)] })] })] }), _jsxs("div", { className: "bg-gray-800/30 rounded-lg p-4 border border-gray-800", children: [_jsx("h3", { className: "text-sm font-medium text-gray-300 mb-2", children: "\u7F18\u7531" }), _jsx("p", { className: "text-sm text-gray-400 whitespace-pre-wrap", children: selectedIntervention.description })] }), selectedIntervention.context && (_jsxs("div", { className: "bg-gray-800/30 rounded-lg p-4 border border-gray-800", children: [_jsx("h3", { className: "text-sm font-medium text-gray-300 mb-2", children: "\u4E0A\u4E0B\u6587\u51ED\u636E" }), _jsx("pre", { className: "text-xs text-gray-400 font-mono overflow-x-auto", children: JSON.stringify(selectedIntervention.context, null, 2) })] }))] }) }), _jsx("div", { className: "p-6 border-t border-gray-800 bg-[var(--ink-indigo)]", children: _jsx("div", { className: "flex items-center justify-end gap-3", children: selectedIntervention.status === 'pending' ? (selectedIntervention.actions?.map((action) => (_jsx(Button, { onClick: () => handleAction(selectedIntervention.id, action.value), variant: action.style === 'danger' ? 'destructive' : action.style === 'secondary' ? 'secondary' : action.style === 'ghost' ? 'ghost' : 'default', className: "min-w-[100px]", children: action.label }, action.value))) || (_jsxs(_Fragment, { children: [_jsx(Button, { variant: "ghost", onClick: () => handleAction(selectedIntervention.id, 'ignore'), children: "\u7565\u8FC7" }), _jsx(Button, { variant: "default", onClick: () => handleAction(selectedIntervention.id, 'approve'), children: "\u51C6\u884C" })] }))) : (_jsxs("div", { className: "text-sm text-gray-500 italic", children: ["\u6B64\u4E8B\u9879\u5DF2\u5904\u7406\uFF1A", getStatusText(selectedIntervention.status), "\u3002"] })) }) })] })) : (_jsxs("div", { className: "flex-1 flex items-center justify-center text-gray-500 flex-col gap-2", children: [_jsx(ShieldAlert, { className: "h-12 w-12 opacity-20" }), _jsx("p", { children: "\u8BF7\u9009\u62E9\u5DE6\u4FA7\u4E8B\u9879\u67E5\u770B\u8BE6\u60C5" })] })) })] })] }) }));
}
