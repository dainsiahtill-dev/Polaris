import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { CodeMap3D } from './CodeMap3D';
import { Card } from '@/app/components/ui/card';
import { Button } from '@/app/components/ui/button';
import { Loader2, RefreshCcw, Box } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/app/components/ui/alert';
import { apiFetch } from '@/api';
export function ArsenalPanel() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const fetchData = useCallback(async (signal) => {
        setLoading(true);
        setError(null);
        try {
            const res = await apiFetch('/arsenal/v2/code_map', { signal });
            if (!res.ok)
                throw new Error('拉取军械库图谱失败');
            const json = await res.json();
            if (signal?.aborted)
                return;
            setData(json);
        }
        catch (err) {
            if (signal?.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
                return;
            }
            const message = err instanceof Error ? err.message : '拉取军械库图谱失败';
            setError(message);
        }
        finally {
            if (!signal?.aborted) {
                setLoading(false);
            }
        }
    }, []);
    useEffect(() => {
        const controller = new AbortController();
        void fetchData(controller.signal);
        return () => controller.abort();
    }, [fetchData]);
    return (_jsxs("div", { className: "space-y-4 text-text-main h-full", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-lg font-semibold flex items-center gap-2", children: [_jsx(Box, { className: "w-5 h-5 text-cyan-400" }), "Polaris \u519B\u68B0\u5E93"] }), _jsx("p", { className: "text-sm text-text-dim", children: "\u91CD\u578B\u53EF\u89C6\u5316\u4E0E\u7B97\u529B\u6A21\u5757" })] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void fetchData(), disabled: loading, className: "border-white/10 hover:bg-white/5", children: [loading ? _jsx(Loader2, { className: "w-4 h-4 animate-spin mr-2" }) : _jsx(RefreshCcw, { className: "w-4 h-4 mr-2" }), "\u91CD\u65B0\u5206\u6790"] })] }), error && (_jsxs(Alert, { variant: "destructive", children: [_jsx(AlertTitle, { children: "\u5F02\u5E38" }), _jsx(AlertDescription, { children: error })] })), _jsxs(Card, { className: "p-4 bg-black/20 border-white/5", children: [_jsxs("div", { className: "mb-4 flex items-center justify-between text-xs text-text-dim", children: [_jsx("span", { children: "\u56FE\u8C31\u89C6\u56FE\uFF1A\u4E09\u7EF4\u4EE3\u7801\u8206\u56FE" }), _jsxs("span", { children: ["\u8FD0\u884C\u6A21\u5F0F\uFF1A", data?.mode?.toUpperCase() || '未判'] })] }), loading && !data ? (_jsx("div", { className: "h-[500px] flex items-center justify-center border border-white/5 rounded bg-black/40", children: _jsxs("div", { className: "flex flex-col items-center gap-2", children: [_jsx(Loader2, { className: "w-8 h-8 animate-spin text-cyan-400" }), _jsx("span", { className: "text-sm text-cyan-400/80", children: "\u6B63\u5728\u5206\u6790\u4EE3\u7801\u7ED3\u6784..." })] }) })) : (data?.points && _jsx(CodeMap3D, { points: data.points })), _jsxs("div", { className: "mt-4 text-xs text-text-muted", children: ["\u5DF2\u7D22\u5F15 ", data?.points?.length || 0, " \u4E2A\u6587\u4EF6\uFF0C\u5F53\u524D\u5F15\u64CE\uFF1A", data?.engine_active ? '扩展引擎' : '标准引擎', "\u3002"] })] })] }));
}
