import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ToolCallRenderer - 工具调用渲染组件
 *
 * 为标准化工具提供图标、颜色和视觉效果：
 * - search_code / ripgrep / grep: 搜索工具
 * - read_file / list_directory / glob / file_exists: 只读工具
 * - write_file / edit_file / search_replace / append_to_file / execute_command: 执行工具
 */
import { useState } from 'react';
import { cn } from '@/app/components/ui/utils';
import { 
// 工具特定图标
GitCompare, Network, Database, Copy, PackageCheck, Activity, Search, 
// 通用图标
ChevronDown, Terminal, CheckCircle2, XCircle, Clock, } from 'lucide-react';
export const TOOL_CONFIGS = {
    // 1. 代码变更分析 - 蓝紫色 Diff 风格
    search_code: {
        name: 'search_code',
        icon: GitCompare,
        color: 'text-indigo-400',
        bgGradient: 'from-indigo-950/40 via-blue-950/30 to-slate-950/50',
        borderColor: 'border-indigo-500/30',
        glowColor: 'shadow-indigo-500/20',
        badgeText: '代码分析',
        description: '分析变更影响范围和风险等级',
    },
    // 2. 语义上下文 - 蓝灰色文件读取风格
    read_file: {
        name: 'read_file',
        icon: Network,
        color: 'text-slate-400',
        bgGradient: 'from-slate-950/60 via-slate-900/40 to-slate-950/50',
        borderColor: 'border-slate-500/25',
        glowColor: 'shadow-slate-500/10',
        badgeText: '语义上下文',
        description: '获取代码结构和依赖关系',
    },
    // 3. 索引构建 - 琥珀色数据库风格
    list_directory: {
        name: 'list_directory',
        icon: Database,
        color: 'text-amber-400',
        bgGradient: 'from-amber-950/40 via-orange-950/30 to-slate-950/50',
        borderColor: 'border-amber-500/30',
        glowColor: 'shadow-amber-500/20',
        badgeText: '索引构建',
        description: '构建或更新代码索引',
    },
    // 4. 相似代码查找 - 粉色复制检测风格
    glob: {
        name: 'glob',
        icon: Copy,
        color: 'text-pink-400',
        bgGradient: 'from-pink-950/40 via-rose-950/30 to-slate-950/50',
        borderColor: 'border-pink-500/30',
        glowColor: 'shadow-pink-500/20',
        badgeText: '相似代码',
        description: '查找重复或相似代码片段',
    },
    // 5. 导入验证 - 绿色检查风格
    file_exists: {
        name: 'file_exists',
        icon: PackageCheck,
        color: 'text-emerald-400',
        bgGradient: 'from-emerald-950/40 via-green-950/30 to-slate-950/50',
        borderColor: 'border-emerald-500/30',
        glowColor: 'shadow-emerald-500/20',
        badgeText: '导入验证',
        description: '验证导入语句正确性',
    },
    // 6. 影响分析 - 红色波纹风格
    grep: {
        name: 'grep',
        icon: Activity,
        color: 'text-rose-400',
        bgGradient: 'from-rose-950/40 via-red-950/30 to-slate-950/50',
        borderColor: 'border-rose-500/30',
        glowColor: 'shadow-rose-500/20',
        badgeText: '影响分析',
        description: '分析变更的级联影响',
    },
    // 7. ripgrep - 靛青色检索风格
    ripgrep: {
        name: 'ripgrep',
        icon: Search,
        color: 'text-indigo-400',
        bgGradient: 'from-slate-950/60 via-slate-900/40 to-slate-950/50',
        borderColor: 'border-slate-500/30',
        glowColor: 'shadow-slate-500/10',
        badgeText: '快速检索',
        description: '基于 ripgrep 的高性能代码搜索',
    },
};
// 通用工具回退配置
const DEFAULT_TOOL_CONFIG = {
    name: 'unknown_tool',
    icon: Terminal,
    color: 'text-slate-400',
    bgGradient: 'from-slate-950/40 via-gray-950/30 to-slate-950/50',
    borderColor: 'border-slate-500/30',
    glowColor: 'shadow-slate-500/20',
    badgeText: '工具调用',
    description: '执行工具操作',
};
function parseToolCall(content) {
    // 尝试匹配 [TOOL_NAME]...[/TOOL_NAME] 格式
    const toolPattern = /\[(\w+)\]([\s\S]*?)\[\/\w+\]/i;
    const match = content.match(toolPattern);
    if (match) {
        const toolName = match[1].toLowerCase();
        const paramsText = match[2].trim();
        // 解析参数 (key: value 格式)
        const params = {};
        const paramLines = paramsText.split('\n');
        for (const line of paramLines) {
            const colonIndex = line.indexOf(':');
            if (colonIndex > 0) {
                const key = line.slice(0, colonIndex).trim();
                let value = line.slice(colonIndex + 1).trim();
                // 尝试解析为 JSON (数组、对象、数字、布尔值)
                if (typeof value === 'string' && (value.startsWith('[') || value.startsWith('{') ||
                    value === 'true' || value === 'false' ||
                    /^\d+$/.test(value))) {
                    try {
                        value = JSON.parse(value);
                    }
                    catch {
                        // 保持为字符串
                    }
                }
                // 去除引号
                if (typeof value === 'string' &&
                    ((value.startsWith('"') && value.endsWith('"')) ||
                        (value.startsWith("'") && value.endsWith("'")))) {
                    value = value.slice(1, -1);
                }
                params[key] = value;
            }
        }
        return { toolName, params, rawContent: content };
    }
    // 尝试匹配 JSON 格式
    try {
        const json = JSON.parse(content);
        if (json.tool || json.name) {
            return {
                toolName: (json.tool || json.name).toLowerCase(),
                params: json.params || json.arguments || {},
                rawContent: content,
            };
        }
    }
    catch {
        // 不是 JSON 格式
    }
    // 无法解析，返回原始内容
    return { toolName: 'unknown', params: {}, rawContent: content };
}
// ═══════════════════════════════════════════════════════════════════════════
// 工具特定动画组件
// ═══════════════════════════════════════════════════════════════════════════
// 获取工具特定的动画组件（已移除装饰性微动画，保留语义图标）
function getToolAnimation(_toolName) {
    return null;
}
export function ToolCallRenderer({ content, className }) {
    const [isExpanded, setIsExpanded] = useState(true);
    const [showRaw, setShowRaw] = useState(false);
    const parsed = parseToolCall(content);
    // 将工具名转换为小写以查找配置（支持大小写）
    const config = TOOL_CONFIGS[parsed.toolName.toLowerCase()] || DEFAULT_TOOL_CONFIG;
    const Icon = config.icon;
    // 格式化参数显示
    const formatParamValue = (value) => {
        if (Array.isArray(value)) {
            return `[${value.length} items]`;
        }
        if (value !== null && typeof value === 'object') {
            return '{...}';
        }
        return String(value);
    };
    return (_jsxs("div", { className: cn('my-3 rounded-lg overflow-hidden border', config.bgGradient ? 'bg-slate-900/60' : 'bg-slate-900/60', config.borderColor, className), children: [_jsxs("button", { onClick: () => setIsExpanded(!isExpanded), className: cn('w-full flex items-center justify-between px-4 py-3', 'transition-colors duration-200', 'hover:bg-white/5'), children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: cn('p-2 rounded-md', 'bg-slate-800/80', 'border border-white/5', config.color), children: _jsx(Icon, { className: "w-4 h-4" }) }), _jsxs("div", { className: "flex flex-col items-start", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: cn('text-sm font-medium', config.color), children: config.badgeText }), _jsx("span", { className: "text-[10px] text-slate-500 font-mono px-1.5 py-0.5 rounded bg-slate-800/50", children: parsed.toolName })] }), _jsx("span", { className: "text-xs text-slate-400", children: config.description })] })] }), _jsx("div", { className: "flex items-center gap-2", children: _jsx(ChevronDown, { className: cn('w-5 h-5 text-slate-400 transition-transform duration-300', isExpanded && 'rotate-180') }) })] }), isExpanded && (_jsxs("div", { className: "border-t border-white/5", children: [Object.keys(parsed.params).length > 0 && (_jsxs("div", { className: "p-4", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-semibold", children: "\u53C2\u6570" }), _jsx("div", { className: "grid gap-2", children: Object.entries(parsed.params).map(([key, value]) => (_jsxs("div", { className: "flex items-center gap-3 text-sm p-2 rounded-lg bg-black/20", children: [_jsx("span", { className: "text-slate-400 font-mono text-xs min-w-[100px]", children: key }), _jsx("span", { className: cn('flex-1 font-mono truncate', Array.isArray(value) ? 'text-amber-300' :
                                                typeof value === 'boolean' ? (value ? 'text-emerald-400' : 'text-rose-400') :
                                                    typeof value === 'number' ? 'text-cyan-300' :
                                                        'text-slate-200'), children: formatParamValue(value) })] }, key))) })] })), _jsxs("div", { className: "px-4 pb-3", children: [_jsxs("button", { onClick: () => setShowRaw(!showRaw), className: "text-[10px] text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1", children: [_jsx(Terminal, { className: "w-3 h-3" }), showRaw ? '隐藏原始数据' : '查看原始数据'] }), showRaw && (_jsx("pre", { className: "mt-2 p-3 rounded-lg bg-black/40 text-xs text-slate-400 font-mono overflow-x-auto", children: _jsx("code", { children: parsed.rawContent }) }))] })] }))] }));
}
// ═══════════════════════════════════════════════════════════════════════════
// 工具结果渲染组件
// ═══════════════════════════════════════════════════════════════════════════
function formatResultForDisplay(result) {
    if (result === null || result === undefined) {
        return '';
    }
    if (typeof result === 'string') {
        return result;
    }
    try {
        return JSON.stringify(result, null, 2);
    }
    catch {
        return String(result);
    }
}
export function ToolResultRenderer({ toolName, result, status, className }) {
    const config = TOOL_CONFIGS[toolName.toLowerCase()] || DEFAULT_TOOL_CONFIG;
    return (_jsxs("div", { className: cn('my-2 rounded-lg overflow-hidden border', status === 'success' && 'border-emerald-500/20 bg-emerald-950/10', status === 'error' && 'border-rose-500/20 bg-rose-950/10', status === 'running' && config.borderColor, className), children: [_jsxs("div", { className: "flex items-center gap-2 px-3 py-2", children: [status === 'success' && _jsx(CheckCircle2, { className: "w-4 h-4 text-emerald-400" }), status === 'error' && _jsx(XCircle, { className: "w-4 h-4 text-rose-400" }), status === 'running' && _jsx(Clock, { className: "w-4 h-4 text-amber-400 animate-pulse" }), _jsxs("span", { className: cn('text-xs', status === 'success' && 'text-emerald-400', status === 'error' && 'text-rose-400', status === 'running' && 'text-amber-400'), children: [status === 'success' && '执行成功', status === 'error' && '执行失败', status === 'running' && '执行中...'] }), _jsx("span", { className: "text-[10px] text-slate-500 font-mono ml-auto", children: config.badgeText })] }), result !== null && result !== undefined && (_jsx("div", { className: "px-3 pb-3", children: _jsx("pre", { className: "p-2 rounded bg-black/30 text-xs text-slate-300 font-mono overflow-x-auto max-h-40 overflow-y-auto", children: _jsx("code", { children: formatResultForDisplay(result) }) }) }))] }));
}
export default ToolCallRenderer;
