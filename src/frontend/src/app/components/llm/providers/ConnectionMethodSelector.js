import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ConnectionMethodSelector Component
 * 连接方式选择器
 */
import { useMemo } from 'react';
import { useProviderActions, useSelectedMethod } from '../state';
const CONNECTION_METHODS = [
    {
        id: 'sdk',
        label: 'SDK 方式',
        description: '官方 SDK 集成，功能完整且稳定',
        pros: ['官方支持', '原生 thinking / streaming', '更好的错误处理', '更完整的功能'],
        cons: ['需要安装 SDK 依赖', '配置项稍多'],
        recommended: true,
        accent: 'bg-status-success/15',
        accentText: 'text-status-success',
        accentBorder: 'border-status-success/45',
    },
    {
        id: 'api',
        label: 'HTTP API 方式',
        description: 'REST API 访问，兼容性最好',
        pros: ['无需 SDK 依赖', '兼容多种服务', '部署简单'],
        cons: ['部分高级功能受限', '流式支持取决于服务端'],
        recommended: false,
        accent: 'bg-accent/15',
        accentText: 'text-accent-text',
        accentBorder: 'border-accent/45',
    },
    {
        id: 'cli',
        label: '命令行方式',
        description: '使用 CLI 工具，适合本地开发',
        pros: ['本地工具链', '参数灵活', '适合快速试用'],
        cons: ['输出解析复杂', '依赖 CLI 安装'],
        recommended: false,
        accent: 'bg-accent/15',
        accentText: 'text-accent-text',
        accentBorder: 'border-accent/45',
    },
];
export function ConnectionMethodSelector({ availableMethods }) {
    const selectedMethod = useSelectedMethod();
    const { selectMethod } = useProviderActions();
    const methods = useMemo(() => {
        if (!availableMethods || availableMethods.length === 0) {
            return CONNECTION_METHODS;
        }
        return CONNECTION_METHODS.filter((m) => availableMethods.includes(m.id));
    }, [availableMethods]);
    return (_jsxs("div", { className: "soft-panel-subtle rounded-lg p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-semibold text-text-main", children: "\u8FDE\u63A5\u65B9\u5F0F\u9009\u62E9" }), _jsx("div", { className: "text-[10px] text-text-dim", children: "\u5148\u9009\u8FDE\u63A5\u65B9\u5F0F\uFF0C\u518D\u9009\u5177\u4F53\u63D0\u4F9B\u5546\u3002" })] }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { children: "\u63A8\u8350\u4F18\u5148\uFF1A" }), _jsx("span", { className: "rounded border border-status-success/40 bg-status-success/10 px-2 py-1 text-status-success", children: "SDK \u65B9\u5F0F" })] })] }), _jsx("div", { className: "mt-4 grid grid-cols-1 md:grid-cols-3 gap-3", children: methods.map((method) => {
                    const selected = selectedMethod === method.id;
                    return (_jsxs("button", { type: "button", onClick: () => selectMethod(method.id), className: `text-left rounded-xl border p-3 transition-all ${selected
                            ? `${method.accentBorder} ${method.accent}`
                            : 'border-border bg-[rgba(6,15,28,0.72)] hover:border-accent/40 hover:bg-accent/10'}`, children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: `text-xs font-semibold ${selected ? method.accentText : 'text-text-main'}`, children: method.label }), method.recommended && (_jsx("span", { className: "rounded-full border border-status-success/40 bg-status-success/10 px-2 py-0.5 text-[9px] text-status-success", children: "\u63A8\u8350" }))] }), _jsx("div", { className: "mt-1 text-[10px] text-text-dim", children: method.description }), _jsxs("div", { className: "mt-2 grid grid-cols-2 gap-2 text-[10px] text-text-dim", children: [_jsxs("div", { className: "space-y-1", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: "\u4F18\u52BF" }), _jsx("div", { className: "flex flex-wrap gap-1", children: method.pros.slice(0, 2).map((item) => (_jsx("span", { className: "soft-chip px-2 py-0.5 text-text-dim", children: item }, item))) })] }), _jsxs("div", { className: "space-y-1", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: "\u9650\u5236" }), _jsx("div", { className: "flex flex-wrap gap-1", children: method.cons.slice(0, 2).map((item) => (_jsx("span", { className: "soft-chip px-2 py-0.5 text-text-dim", children: item }, item))) })] })] })] }, method.id));
                }) })] }));
}
export { CONNECTION_METHODS };
