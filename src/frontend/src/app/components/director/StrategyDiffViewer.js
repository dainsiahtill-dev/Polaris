import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/** StrategyDiffViewer - 策略变更对比查看器
 *
 * 功能：
 * - 策略 diff/变更显示
 * - 版本历史对比
 */
import { useState, useMemo } from 'react';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import { GitCompare, ChevronRight, Clock, User, Tag, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
export function StrategyDiffViewer({ versions = [], leftVersion, rightVersion, onSelectVersion, splitView = true, }) {
    const [selectedLeft, setSelectedLeft] = useState(leftVersion || (versions[0]?.id ?? ''));
    const [selectedRight, setSelectedRight] = useState(rightVersion || (versions[1]?.id ?? versions[0]?.id ?? ''));
    const [ignoreWhitespace, setIgnoreWhitespace] = useState(false);
    const [splitViewEnabled, setSplitViewEnabled] = useState(splitView);
    const leftContent = useMemo(() => {
        const v = versions.find(v => v.id === selectedLeft);
        return v?.content ?? '';
    }, [versions, selectedLeft]);
    const rightContent = useMemo(() => {
        const v = versions.find(v => v.id === selectedRight);
        return v?.content ?? '';
    }, [versions, selectedRight]);
    const handleVersionSelect = (side, versionId) => {
        if (side === 'left') {
            setSelectedLeft(versionId);
        }
        else {
            setSelectedRight(versionId);
        }
        if (onSelectVersion) {
            const other = side === 'left' ? selectedRight : selectedLeft;
            onSelectVersion(side === 'left' ? versionId : other, side === 'right' ? versionId : other);
        }
    };
    return (_jsxs("div", { className: "h-full flex flex-col bg-[linear-gradient(165deg,rgba(15,23,42,0.96),rgba(30,27,75,0.74),rgba(8,15,31,0.98))]", "data-testid": "strategy-diff-viewer", children: [_jsxs("div", { className: "h-14 flex items-center justify-between px-4 border-b border-indigo-400/20", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "w-8 h-8 rounded-lg bg-cyan-500/[0.15] border border-cyan-400/25 flex items-center justify-center shadow-lg shadow-cyan-500/10", children: _jsx(GitCompare, { className: "w-4 h-4 text-cyan-100" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-sm font-semibold text-cyan-100", children: "\u53D8\u66F4\u5BF9\u6BD4" }), _jsx("p", { className: "text-[10px] text-cyan-400/60 uppercase tracking-wider", children: "Diff Viewer" })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Button, { variant: "outline", size: "sm", onClick: () => setIgnoreWhitespace(!ignoreWhitespace), className: cn('border-cyan-400/30 text-cyan-400 hover:bg-cyan-500/10', ignoreWhitespace && 'bg-cyan-500/20'), children: "\u5FFD\u7565\u7A7A\u767D" }), _jsx(Button, { variant: "outline", size: "sm", onClick: () => setSplitViewEnabled(!splitViewEnabled), className: cn('border-cyan-400/30 text-cyan-400 hover:bg-cyan-500/10', splitViewEnabled && 'bg-cyan-500/20'), children: splitViewEnabled ? '分屏' : '单屏' })] })] }), versions.length >= 2 && (_jsxs("div", { className: "h-12 flex items-center gap-4 px-4 border-b border-indigo-400/10 bg-cyan-500/5", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-[10px] text-cyan-200/60", children: "\u5BF9\u6BD4:" }), _jsx("select", { value: selectedLeft, onChange: (e) => handleVersionSelect('left', e.target.value), className: "h-7 px-2 rounded bg-slate-950/80 border border-cyan-400/20 text-xs text-cyan-200", children: versions.map((v) => (_jsxs("option", { value: v.id, children: ["v", v.version, " - ", new Date(v.timestamp).toLocaleDateString()] }, v.id))) })] }), _jsx(ChevronRight, { className: "w-4 h-4 text-cyan-400/50" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-[10px] text-cyan-200/60", children: "\u5230:" }), _jsx("select", { value: selectedRight, onChange: (e) => handleVersionSelect('right', e.target.value), className: "h-7 px-2 rounded bg-slate-950/80 border border-cyan-400/20 text-xs text-cyan-200", children: versions.map((v) => (_jsxs("option", { value: v.id, children: ["v", v.version, " - ", new Date(v.timestamp).toLocaleDateString()] }, v.id))) })] })] })), _jsx("div", { className: "flex-1 overflow-hidden", children: versions.length >= 2 ? (_jsx(ReactDiffViewer, { oldValue: leftContent, newValue: rightContent, splitView: splitViewEnabled, hideLineNumbers: false, compareMethod: DiffMethod.WORDS, useDarkTheme: true, styles: {
                        diffContainer: {
                            background: 'rgba(15, 23, 42, 0.82)',
                        },
                        line: {
                            padding: '2px 8px',
                        },
                        gutter: {
                            padding: '2px 8px',
                            minWidth: '50px',
                        },
                        contentText: {
                            fontSize: '12px',
                            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                        },
                    } })) : (_jsxs("div", { className: "h-full flex flex-col items-center justify-center text-cyan-400/50", children: [_jsx(GitCompare, { className: "w-12 h-12 mb-4 opacity-30" }), _jsx("p", { children: "\u9700\u8981\u81F3\u5C11\u4E24\u4E2A\u7248\u672C\u624D\u80FD\u5BF9\u6BD4" }), _jsx("p", { className: "text-xs mt-2 opacity-70", children: "\u4FDD\u5B58\u7B56\u7565\u7248\u672C\u540E\u5373\u53EF\u67E5\u770B\u53D8\u66F4" })] })) }), versions.length > 0 && (_jsxs("div", { className: "h-10 flex items-center justify-between px-4 border-t border-amber-400/10 bg-slate-900/30 text-[10px] text-cyan-200/50", children: [_jsx("div", { className: "flex items-center gap-4", children: versions.find(v => v.id === selectedRight) && (_jsxs(_Fragment, { children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Tag, { className: "w-3 h-3" }), "v", versions.find(v => v.id === selectedRight)?.version] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Clock, { className: "w-3 h-3" }), versions.find(v => v.id === selectedRight)?.timestamp
                                            ? new Date(versions.find(v => v.id === selectedRight).timestamp).toLocaleString()
                                            : '-'] }), versions.find(v => v.id === selectedRight)?.author && (_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(User, { className: "w-3 h-3" }), versions.find(v => v.id === selectedRight)?.author] }))] })) }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx("span", { className: "w-2 h-2 rounded bg-emerald-500/50" }), _jsx("span", { children: "\u65B0\u589E" })] }), _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx("span", { className: "w-2 h-2 rounded bg-red-500/50" }), _jsx("span", { children: "\u5220\u9664" })] })] })] }))] }));
}
