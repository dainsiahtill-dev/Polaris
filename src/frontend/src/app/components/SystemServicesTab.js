import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * System Services Tab — displays status of backend services:
 * - MCP Policy Server (MCP Policy)
 * - Code Search Engine (Code Search)
 * - Director Capabilities (Director Capabilities)
 * - Vision Service (视觉服务)
 */
import { useState, useEffect, useCallback } from 'react';
import { Shield, Search, Eye, Cpu, RefreshCw, CheckCircle, XCircle, Loader2, Terminal, FileCode } from 'lucide-react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';
export function normalizeCapabilityLabels(capabilities) {
    const labels = new Set();
    if (Array.isArray(capabilities)) {
        for (const capability of capabilities) {
            const label = String(capability || '').trim();
            if (label)
                labels.add(label);
        }
        return Array.from(labels).sort();
    }
    if (!capabilities || typeof capabilities !== 'object') {
        return [];
    }
    for (const [hostKind, values] of Object.entries(capabilities)) {
        if (Array.isArray(values)) {
            for (const capability of values) {
                const name = String(capability || '').trim();
                if (name)
                    labels.add(`${hostKind}: ${name}`);
            }
            continue;
        }
        if (values && typeof values === 'object') {
            for (const [name, enabled] of Object.entries(values)) {
                if (enabled)
                    labels.add(`${hostKind}: ${name}`);
            }
        }
    }
    return Array.from(labels).sort();
}
export function SystemServicesTab() {
    const [services, setServices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState([]);
    const [searching, setSearching] = useState(false);
    const [indexing, setIndexing] = useState(false);
    const fetchStatus = useCallback(async () => {
        setLoading(true);
        const results = [];
        // MCP Status
        try {
            const res = await apiFetch('/arsenal/v2/mcp/status');
            const data = await res.json();
            results.push({
                name: 'MCP Policy Service',
                icon: _jsx(Shield, { className: "w-4 h-4" }),
                status: data.available ? 'online' : 'offline',
                detail: data.available
                    ? `${data.tools?.length || 0} 项器用可用`
                    : '未见服务',
                extra: data,
            });
        }
        catch {
            results.push({
                name: 'MCP Policy Service',
                icon: _jsx(Shield, { className: "w-4 h-4" }),
                status: 'unknown',
                detail: '暂无法核验',
            });
        }
        // Director Capabilities
        try {
            const res = await apiFetch('/arsenal/v2/director/capabilities');
            const data = await res.json();
            const capabilityLabels = normalizeCapabilityLabels(data.capabilities);
            results.push({
                name: 'Director Capabilities Overview',
                icon: _jsx(Cpu, { className: "w-4 h-4" }),
                status: capabilityLabels.length > 0 ? 'online' : 'offline',
                detail: capabilityLabels.length
                    ? `${capabilityLabels.length} 项权限已启用`
                    : '尚未配置',
                extra: {
                    ...data,
                    capabilities: capabilityLabels,
                    capability_matrix: data.capabilities,
                },
            });
        }
        catch {
            results.push({
                name: 'Director Capabilities Overview',
                icon: _jsx(Cpu, { className: "w-4 h-4" }),
                status: 'unknown',
                detail: '暂无法核验',
            });
        }
        // Vision Service
        try {
            const res = await apiFetch('/arsenal/v2/vision/status');
            const data = await res.json();
            results.push({
                name: '视察司服务',
                icon: _jsx(Eye, { className: "w-4 h-4" }),
                status: data.pil_available ? 'online' : 'offline',
                detail: data.model_loaded
                    ? `模型：${data.model_name}`
                    : data.pil_available
                        ? '基础模式（PIL）'
                        : '不可用',
                extra: data,
            });
        }
        catch {
            results.push({
                name: '视察司服务',
                icon: _jsx(Eye, { className: "w-4 h-4" }),
                status: 'unknown',
                detail: '暂无法核验',
            });
        }
        // Code Search
        results.push({
            name: 'Code Search Engine',
            icon: _jsx(Search, { className: "w-4 h-4" }),
            status: 'online',
            detail: '已就绪，先为 workspace 索引后可检索',
        });
        setServices(results);
        setLoading(false);
    }, []);
    useEffect(() => {
        let mounted = true;
        fetchStatus().then(() => {
            if (mounted)
                return;
            // silently ignore - component unmounted
        }).catch(() => {
            if (!mounted)
                return;
            // silently ignore - component unmounted
        });
        return () => { mounted = false; };
    }, [fetchStatus]);
    const handleIndex = async () => {
        setIndexing(true);
        try {
            const res = await apiFetch('/arsenal/v2/code/index', { method: 'POST' });
            const data = await res.json();
            if (data.ok) {
                toast.success(`Indexed ${data.files} files (${data.chunks} chunks)`);
            }
            else {
                toast.error(`Index failed: ${data.error}`);
            }
        }
        catch (e) {
            const errorMessage = e instanceof Error ? e.message : String(e);
            toast.error(`Index error: ${errorMessage}`);
        }
        setIndexing(false);
        fetchStatus();
    };
    const handleSearch = async () => {
        if (!searchQuery.trim())
            return;
        setSearching(true);
        try {
            const res = await apiFetch('/arsenal/v2/code/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: searchQuery, limit: 20 }),
            });
            const data = await res.json();
            setSearchResults(data.results || []);
        }
        catch {
            setSearchResults([]);
        }
        setSearching(false);
    };
    const statusIcon = (s) => {
        if (s === 'online')
            return _jsx(CheckCircle, { className: "w-3.5 h-3.5 text-emerald-400" });
        if (s === 'offline')
            return _jsx(XCircle, { className: "w-3.5 h-3.5 text-red-400" });
        if (s === 'loading')
            return _jsx(Loader2, { className: "w-3.5 h-3.5 text-yellow-400 animate-spin" });
        return _jsx(XCircle, { className: "w-3.5 h-3.5 text-gray-400" });
    };
    return (_jsxs("div", { className: "space-y-6 p-4", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "text-sm font-semibold text-cyan-300 tracking-wide flex items-center gap-2", children: [_jsx(Terminal, { className: "w-4 h-4" }), "\u5185\u52A1\u53F8\u603B\u89C8\uFF08\u516D\u90E8\u804C\u80FD\uFF09"] }), _jsxs("button", { onClick: fetchStatus, disabled: loading, className: "text-xs text-gray-400 hover:text-cyan-400 flex items-center gap-1 transition-colors", children: [_jsx(RefreshCw, { className: `w-3 h-3 ${loading ? 'animate-spin' : ''}` }), "\u5237\u65B0"] })] }), _jsx("div", { className: "grid grid-cols-1 gap-3", children: services.map((svc, idx) => (_jsxs("div", { className: "bg-black/40 backdrop-blur-sm rounded-lg border border-cyan-500/20 p-3 flex items-center gap-3", children: [_jsx("div", { className: "text-cyan-400", children: svc.icon }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-xs font-medium text-gray-200", children: svc.name }), _jsx("div", { className: "text-[10px] text-gray-400 truncate", children: svc.detail }), svc.extra?.capabilities && (_jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: svc.extra.capabilities.map((cap) => (_jsx("span", { className: "text-[9px] bg-cyan-500/10 text-cyan-300 px-1.5 py-0.5 rounded border border-cyan-500/20", children: cap }, cap))) })), svc.extra?.tools && (_jsx("div", { className: "mt-1 flex flex-wrap gap-1", children: svc.extra.tools.map((tool) => (_jsx("span", { className: "text-[9px] bg-purple-500/10 text-purple-300 px-1.5 py-0.5 rounded border border-purple-500/20", children: tool }, tool))) }))] }), statusIcon(svc.status)] }, idx))) }), _jsxs("div", { className: "bg-black/40 backdrop-blur-sm rounded-lg border border-cyan-500/20 p-4 space-y-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(FileCode, { className: "w-4 h-4 text-cyan-400" }), _jsx("h4", { className: "text-xs font-semibold text-cyan-300", children: "Code Search" })] }), _jsx("div", { className: "flex gap-2", children: _jsxs("button", { onClick: handleIndex, disabled: indexing, className: "text-xs bg-purple-500/20 hover:bg-purple-500/30 text-purple-300 px-3 py-1.5 rounded border border-purple-500/30 flex items-center gap-1 transition-colors disabled:opacity-50", children: [indexing ? _jsx(Loader2, { className: "w-3 h-3 animate-spin" }) : _jsx(RefreshCw, { className: "w-3 h-3" }), indexing ? '索引中...' : '为 Workspace 索引'] }) }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { type: "text", value: searchQuery, onChange: e => setSearchQuery(e.target.value), onKeyDown: e => e.key === 'Enter' && handleSearch(), placeholder: "\u641C\u7D22\u7ECF\u7C4D\u4E0E\u4EE3\u7801...", className: "flex-1 text-xs bg-black/60 border border-cyan-500/20 rounded px-3 py-1.5 text-gray-200 placeholder:text-gray-500 focus:border-cyan-500/50 focus:outline-none" }), _jsxs("button", { onClick: handleSearch, disabled: searching || !searchQuery.trim(), className: "text-xs bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 px-3 py-1.5 rounded border border-cyan-500/30 flex items-center gap-1 transition-colors disabled:opacity-50", children: [searching ? _jsx(Loader2, { className: "w-3 h-3 animate-spin" }) : _jsx(Search, { className: "w-3 h-3" }), "\u641C\u7D22"] })] }), searchResults.length > 0 && (_jsx("div", { className: "max-h-64 overflow-y-auto space-y-2", children: searchResults.map((r, i) => (_jsxs("div", { className: "bg-black/30 rounded border border-gray-700/50 p-2", children: [_jsxs("div", { className: "text-[10px] text-cyan-300 font-mono", children: [r.file_path, ":", r.line_start, "-", r.line_end] }), _jsx("pre", { className: "text-[10px] text-gray-400 font-mono mt-1 whitespace-pre-wrap max-h-20 overflow-hidden", children: r.text?.slice(0, 300) })] }, i))) }))] })] }));
}
