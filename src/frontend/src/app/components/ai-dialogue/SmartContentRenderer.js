import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * SmartContentRenderer - 智能内容渲染组件
 *
 * 解析并渲染 AI 输出中的特殊标签：
 * - <thinking> - 思考过程（紫色科技感）
 * - <output> - 最终输出（青色发光效果）
 * - <tool_call> - 工具调用（橙色机械感）
 * - <error> - 错误信息（红色警示）
 * - <warning> - 警告（黄色提醒）
 */
import { useState } from 'react';
import { cn } from '@/app/components/ui/utils';
import { Brain, AlertCircle, AlertTriangle, ChevronDown, Sparkles, } from 'lucide-react';
import { ToolCallRenderer } from './ToolCallRenderer';
// 标准工具标签列表（支持大小写）
const STANDARD_TOOL_TAGS = [
    'search_code', 'SEARCH_CODE',
    'grep', 'GREP',
    'ripgrep', 'RIPGREP',
    'read_file', 'READ_FILE',
    'write_file', 'WRITE_FILE',
    'execute_command', 'EXECUTE_COMMAND',
    'search_replace', 'SEARCH_REPLACE',
    'edit_file', 'EDIT_FILE',
    'append_to_file', 'APPEND_TO_FILE',
    'list_directory', 'LIST_DIRECTORY',
    'glob', 'GLOB',
    'file_exists', 'FILE_EXISTS',
];
// 解析内容中的标签（支持 XML 标签和方括号工具调用）
function parseContent(content) {
    const segments = [];
    // 构建完整的正则表达式：
    // 1. XML 标签: <thinking>...</thinking>, <output>...</output> 等
    // 2. 工具调用: [TOOL_NAME]...[/TOOL_NAME]
    const toolNamesPattern = STANDARD_TOOL_TAGS.join('|');
    const xmlTagPattern = '<(thinking|output|tool_call|error|warning)[^>]*>([\\s\\S]*?)<\\/\\1>';
    const bracketToolPattern = `\\[(${toolNamesPattern})\\]([\\s\\S]*?)\\[/\\1\\]`;
    const combinedPattern = new RegExp(`${xmlTagPattern}|${bracketToolPattern}`, 'gi');
    // 检查是否有 <output> 标签 - 如果有，优先只使用 output 内的内容
    const outputPattern = /<output[^>]*>([\s\S]*?)<\/output>/i;
    const outputMatch = content.match(outputPattern);
    if (outputMatch) {
        // 有 <output> 标签，只解析标签内的内容，忽略标签外的重复
        const outputContent = outputMatch[1].trim();
        // 检查 output 内是否还有嵌套标签
        let innerLastIndex = 0;
        let innerMatch;
        while ((innerMatch = combinedPattern.exec(outputContent)) !== null) {
            // 添加标签前的文本
            if (innerMatch.index > innerLastIndex) {
                const plainText = outputContent.slice(innerLastIndex, innerMatch.index).trim();
                if (plainText) {
                    segments.push({ type: 'plain', content: plainText });
                }
            }
            // 处理嵌套标签
            let tagType;
            let tagContent;
            if (innerMatch[1]) {
                tagType = innerMatch[1].toLowerCase();
                tagContent = innerMatch[2].trim();
            }
            else if (innerMatch[3]) {
                tagType = 'tool_call';
                const toolName = innerMatch[3];
                const toolParams = innerMatch[4].trim();
                tagContent = `[${toolName}]\n${toolParams}\n[/${toolName}]`;
            }
            else {
                tagType = 'plain';
                tagContent = innerMatch[0];
            }
            segments.push({ type: tagType, content: tagContent });
            innerLastIndex = innerMatch.index + innerMatch[0].length;
        }
        // 添加剩余文本
        if (innerLastIndex < outputContent.length) {
            const remainingText = outputContent.slice(innerLastIndex).trim();
            if (remainingText) {
                segments.push({ type: 'plain', content: remainingText });
            }
        }
        // 如果 output 内没有解析到任何内容，把整个 output 内容作为 plain
        if (segments.length === 0) {
            segments.push({ type: 'plain', content: outputContent });
        }
        return segments;
    }
    // 没有 <output> 标签，使用原来的解析逻辑
    let lastIndex = 0;
    let match;
    while ((match = combinedPattern.exec(content)) !== null) {
        // 添加标签前的普通文本
        if (match.index > lastIndex) {
            const plainText = content.slice(lastIndex, match.index).trim();
            if (plainText) {
                segments.push({ type: 'plain', content: plainText });
            }
        }
        // 判断匹配类型
        let tagType;
        let tagContent;
        if (match[1]) {
            // XML 标签匹配 (match[1] 是标签名)
            tagType = match[1].toLowerCase();
            tagContent = match[2].trim();
        }
        else if (match[3]) {
            // 方括号工具调用匹配 (match[3] 是工具名)
            tagType = 'tool_call';
            const toolName = match[3];
            const toolParams = match[4].trim();
            tagContent = `[${toolName}]\n${toolParams}\n[/${toolName}]`;
        }
        else {
            // 未知匹配，作为普通文本
            tagType = 'plain';
            tagContent = match[0];
        }
        segments.push({ type: tagType, content: tagContent });
        lastIndex = match.index + match[0].length;
    }
    // 添加剩余文本
    if (lastIndex < content.length) {
        const remainingText = content.slice(lastIndex).trim();
        if (remainingText) {
            segments.push({ type: 'plain', content: remainingText });
        }
    }
    // 如果没有匹配到任何标签，返回原内容
    if (segments.length === 0) {
        segments.push({ type: 'plain', content: content.trim() });
    }
    return segments;
}
// ═══════════════════════════════════════════════════════════════════════════
// 各个标签的渲染组件
// ═══════════════════════════════════════════════════════════════════════════
// 1. Thinking 标签 - 紫色科技感，脑电波效果
function ThinkingBlock({ content }) {
    const [isExpanded, setIsExpanded] = useState(true);
    return (_jsxs("div", { className: "my-3 rounded-xl overflow-hidden border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-purple-950/30 to-slate-950/50", children: [_jsxs("button", { onClick: () => setIsExpanded(!isExpanded), className: "w-full flex items-center justify-between px-4 py-2.5 bg-violet-500/10 hover:bg-violet-500/[0.15] transition-colors", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: "relative", children: [_jsx(Brain, { className: "w-4 h-4 text-violet-400" }), _jsx("span", { className: "absolute -top-0.5 -right-0.5 w-1.5 h-1.5 bg-violet-400 rounded-full animate-pulse" })] }), _jsx("span", { className: "text-xs font-medium text-violet-300", children: "\u601D\u8003\u8FC7\u7A0B" }), _jsx(Sparkles, { className: "w-3 h-3 text-violet-400/60" })] }), _jsx(ChevronDown, { className: cn('w-4 h-4 text-violet-400 transition-transform duration-300', isExpanded && 'rotate-180') })] }), isExpanded && (_jsxs("div", { className: "relative", children: [_jsx("div", { className: "absolute left-0 top-0 bottom-0 w-0.5 bg-gradient-to-b from-violet-500/50 via-purple-500/30 to-transparent" }), _jsxs("div", { className: "p-4 pl-5", children: [_jsx("div", { className: "flex gap-0.5 mb-3 opacity-40", children: [...Array(12)].map((_, i) => (_jsx("div", { className: "w-0.5 bg-violet-400 rounded-full animate-pulse", style: {
                                        height: `${Math.random() * 16 + 8}px`,
                                        animationDelay: `${i * 0.1}s`,
                                        animationDuration: `${0.8 + Math.random() * 0.4}s`
                                    } }, i))) }), _jsx("p", { className: "text-sm text-violet-200/80 whitespace-pre-wrap leading-relaxed", children: content })] })] }))] }));
}
// 2. Output 标签 - 青色发光效果，重要输出
function OutputBlock({ content }) {
    return (_jsxs("div", { className: "my-3 relative", children: [_jsx("div", { className: "absolute -inset-0.5 bg-gradient-to-r from-cyan-500/20 via-teal-500/20 to-emerald-500/20 rounded-xl blur-sm" }), _jsxs("div", { className: "relative rounded-xl border border-cyan-500/30 bg-gradient-to-br from-cyan-950/30 via-teal-950/20 to-slate-900/50 overflow-hidden", children: [_jsx("div", { className: "h-1 bg-gradient-to-r from-cyan-500/60 via-teal-500/40 to-emerald-500/30" }), _jsxs("div", { className: "relative p-4", children: [_jsx("div", { className: "absolute top-2 right-2 w-8 h-8 border-t border-r border-cyan-500/20 rounded-tr-lg" }), _jsx("div", { className: "absolute bottom-2 left-2 w-8 h-8 border-b border-l border-cyan-500/20 rounded-bl-lg" }), _jsx("p", { className: "text-sm text-cyan-100/90 whitespace-pre-wrap leading-relaxed", children: content })] })] })] }));
}
// 3. Tool Call 标签 - 使用专门的工具渲染器
function ToolCallBlock({ content }) {
    return _jsx(ToolCallRenderer, { content: content });
}
// 4. Error 标签 - 红色警示，故障效果
function ErrorBlock({ content }) {
    return (_jsxs("div", { className: "my-3 rounded-xl overflow-hidden border border-red-500/40 bg-gradient-to-br from-red-950/50 via-rose-950/30 to-slate-950/50", children: [_jsx("div", { className: "absolute inset-0 opacity-5", children: _jsx("div", { className: "h-full w-full bg-[repeating-linear-gradient(0deg,transparent,transparent_2px,#f00_2px,#f00_4px)]" }) }), _jsxs("div", { className: "relative flex items-center gap-2 px-4 py-3 bg-red-500/[0.15] border-b border-red-500/20", children: [_jsxs("div", { className: "relative", children: [_jsx(AlertCircle, { className: "w-5 h-5 text-red-400" }), _jsx("span", { className: "absolute inset-0 bg-red-400/30 rounded-full animate-ping", style: { animationDuration: '1.5s' } })] }), _jsx("span", { className: "text-sm font-semibold text-red-300", children: "\u6267\u884C\u9519\u8BEF" }), _jsx("span", { className: "ml-auto text-[10px] text-red-400/50 font-mono px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/20", children: "ERROR" })] }), _jsxs("div", { className: "relative p-4", children: [_jsx("div", { className: "flex gap-1 mb-3", children: [...Array(3)].map((_, i) => (_jsx("div", { className: "h-1 w-8 bg-red-500/30 rounded-full", style: { opacity: 1 - i * 0.25 } }, i))) }), _jsx("p", { className: "text-sm text-red-200/80 whitespace-pre-wrap font-mono leading-relaxed", children: content })] })] }));
}
// 5. Warning 标签 - 黄色提醒，警示效果
function WarningBlock({ content }) {
    return (_jsxs("div", { className: "my-3 rounded-lg overflow-hidden border border-yellow-500/30 bg-gradient-to-br from-yellow-950/30 via-amber-950/20 to-slate-950/50", children: [_jsx("div", { className: "h-1.5 bg-[repeating-linear-gradient(45deg,transparent,transparent_10px,rgba(234,179,8,0.2)_10px,rgba(234,179,8,0.2)_20px)]" }), _jsxs("div", { className: "flex items-center gap-2 px-3 py-2 bg-yellow-500/10 border-b border-yellow-500/10", children: [_jsx(AlertTriangle, { className: "w-4 h-4 text-yellow-400" }), _jsx("span", { className: "text-xs font-medium text-yellow-300", children: "\u8B66\u544A" })] }), _jsx("div", { className: "p-3", children: _jsx("p", { className: "text-sm text-yellow-200/80 whitespace-pre-wrap leading-relaxed", children: content }) })] }));
}
// 6. 普通文本 - 默认渲染
function PlainBlock({ content }) {
    return (_jsx("p", { className: "whitespace-pre-wrap leading-relaxed", children: content }));
}
export function SmartContentRenderer({ content, className }) {
    const segments = parseContent(content);
    if (segments.length === 0) {
        return null;
    }
    // 如果只包含纯文本，直接渲染
    if (segments.length === 1 && segments[0].type === 'plain') {
        return (_jsx("div", { className: className, children: _jsx(PlainBlock, { content: segments[0].content }) }));
    }
    return (_jsx("div", { className: cn('space-y-1', className), children: segments.map((segment, index) => {
            const key = `${segment.type}-${index}`;
            switch (segment.type) {
                case 'thinking':
                    return _jsx(ThinkingBlock, { content: segment.content }, key);
                case 'output':
                    return _jsx(OutputBlock, { content: segment.content }, key);
                case 'tool_call':
                    return _jsx(ToolCallBlock, { content: segment.content }, key);
                case 'error':
                    return _jsx(ErrorBlock, { content: segment.content }, key);
                case 'warning':
                    return _jsx(WarningBlock, { content: segment.content }, key);
                default:
                    return _jsx(PlainBlock, { content: segment.content }, key);
            }
        }) }));
}
export default SmartContentRenderer;
