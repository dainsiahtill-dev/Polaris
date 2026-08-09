import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { CheckCircle2, AlertTriangle, Loader2, PlayCircle, Trash2, Edit3, ChevronDown, ChevronUp, Terminal, Clock, UserCheck, UserX, HelpCircle, Zap, Shield, Key, Eye } from 'lucide-react';
import { useState, useMemo } from 'react';
import { PROVIDER_LABELS, STATUS_BADGES, INTERVIEW_BADGES, INTERVIEW_STATUS, isCLIProvider, isCodexCLIProvider, isCLIConnection, isHTTPConnection } from './types';
export function SimpleModelCard({ provider, onUpdate, onDelete, onTest, renderModelBrowser, onOpenTuiBrowser, onViewTestReport }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [editForm, setEditForm] = useState(provider);
    const isCodexCli = isCodexCLIProvider(provider.kind, provider.conn);
    const cliMode = provider.cliMode || 'headless';
    const usesOutputPath = isCLIConnection(provider.conn) &&
        (provider.conn.args || []).some((arg) => arg.includes('{output}'));
    const applyCodexPreset = () => {
        setEditForm((prev) => ({
            ...prev,
            name: prev.name && prev.name.trim() ? prev.name : 'Codex CLI',
            kind: 'codex_cli',
            cliMode: 'headless',
            conn: {
                kind: 'codex_cli',
                command: 'codex',
                args: ['exec', '--skip-git-repo-check', '--color', 'never', '--model', '{model}', '--json', '{prompt}'],
                env: (prev.conn.kind === 'codex_cli' || prev.conn.kind === 'gemini_cli') ? prev.conn.env : {}
            }
        }));
    };
    const getInterviewIcon = (status) => {
        switch (status) {
            case INTERVIEW_STATUS.PASSED:
                return _jsx(UserCheck, { className: "size-3 text-green-400" });
            case INTERVIEW_STATUS.FAILED:
                return _jsx(UserX, { className: "size-3 text-red-400" });
            default:
                return _jsx(HelpCircle, { className: "size-3 text-gray-400" });
        }
    };
    const getInterviewLabel = (status) => {
        switch (status) {
            case INTERVIEW_STATUS.PASSED:
                return '面试通过';
            case INTERVIEW_STATUS.FAILED:
                return '面试失败';
            default:
                return '未测试';
        }
    };
    const providerType = useMemo(() => {
        if (isCLIConnection(provider.conn)) {
            return cliMode === 'tui' ? 'TUI' : 'CLI';
        }
        return 'HTTP';
    }, [provider.conn, cliMode]);
    const authType = useMemo(() => {
        if (isCLIConnection(provider.conn)) {
            return '无';
        }
        if (provider.conn.kind === 'http' && provider.conn.apiKey) {
            return 'API 密钥';
        }
        return '无';
    }, [provider.conn]);
    const providerFeatures = useMemo(() => {
        const features = [];
        if (isCLIProvider(provider.kind)) {
            features.push('CLI');
            if (cliMode === 'tui') {
                features.push('TUI');
            }
        }
        if (isHTTPConnection(provider.conn)) {
            features.push('REST API');
        }
        if (provider.costClass === 'LOCAL') {
            features.push('本地');
        }
        if (provider.costClass === 'METERED') {
            features.push('按量');
        }
        return features;
    }, [provider.kind, provider.conn, cliMode, provider.costClass]);
    const handleSaveEdit = () => {
        onUpdate(editForm);
        setIsEditing(false);
    };
    const handleCancelEdit = () => {
        setEditForm(provider);
        setIsEditing(false);
    };
    const renderStatusIndicator = () => {
        switch (provider.status) {
            case 'ready':
                return _jsx(CheckCircle2, { className: "size-4 text-emerald-400" });
            case 'testing':
                return _jsx(Loader2, { className: "size-4 text-blue-400 animate-spin" });
            case 'failed':
                return _jsx(AlertTriangle, { className: "size-4 text-red-400" });
            default:
                return _jsx("div", { className: "size-4 rounded-full bg-gray-500/60" });
        }
    };
    const renderCompactView = () => (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-3", children: [renderStatusIndicator(), _jsxs("div", { children: [_jsx("h4", { className: "text-sm font-semibold text-text-main", children: provider.name }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "font-mono", children: provider.modelId || "默认" }), provider.costClass && (_jsxs(_Fragment, { children: [_jsx("span", { children: "\u2022" }), _jsx("span", { className: "text-amber-400", children: provider.costClass })] }))] })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: "flex items-center gap-1.5 px-2 py-1 rounded border border-white/10 bg-white/5", children: [getInterviewIcon(provider.interviewStatus), _jsx("span", { className: "text-[10px] text-text-main", children: getInterviewLabel(provider.interviewStatus) })] }), _jsxs("button", { onClick: onTest, disabled: provider.status === 'testing', className: "px-3 py-1.5 text-[10px] font-semibold bg-cyan-500/[0.08]0 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1", children: [provider.status === 'testing' ? (_jsx(Loader2, { className: "size-3 animate-spin" })) : (_jsx(PlayCircle, { className: "size-3" })), "Test"] }), _jsx("button", { onClick: () => setIsExpanded(!isExpanded), className: "p-1.5 rounded border border-white/10 hover:border-accent/40 transition-colors", children: isExpanded ? _jsx(ChevronUp, { className: "size-3" }) : _jsx(ChevronDown, { className: "size-3" }) })] })] }), provider.lastError && (_jsx("div", { className: "text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2", children: provider.lastError }))] }));
    const renderExpandedView = () => (_jsxs("div", { className: "space-y-4 pt-4 border-t border-white/10", children: [_jsxs("div", { className: "grid grid-cols-3 gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5", children: [_jsx(Zap, { className: "size-3.5 text-amber-400" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u7C7B\u578B" }), _jsx("div", { className: "text-xs text-text-main truncate", children: providerType })] })] }), _jsxs("div", { className: "flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5", children: [_jsx(Key, { className: "size-3.5 text-cyan-400" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u8BA4\u8BC1" }), _jsx("div", { className: "text-xs text-text-main truncate", children: authType })] })] }), _jsxs("div", { className: "flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5", children: [_jsx(Shield, { className: "size-3.5 text-green-400" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-[9px] text-text-dim uppercase tracking-wide", children: "\u7279\u6027" }), _jsx("div", { className: "text-xs text-text-main truncate", children: providerFeatures.join(', ') || '-' })] })] })] }), provider.interviewStatus && (_jsxs("div", { className: "space-y-3", children: [_jsxs("h5", { className: "text-xs font-semibold text-text-main flex items-center gap-2", children: [_jsx(UserCheck, { className: "size-3.5 text-accent" }), "\u9762\u8BD5\u8BB0\u5F55"] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${INTERVIEW_BADGES[provider.interviewStatus]}`, children: provider.interviewStatus.toUpperCase() }), provider.lastInterviewAt && (_jsxs("span", { className: "flex items-center gap-1 text-[10px] text-text-dim", children: [_jsx(Clock, { className: "size-3" }), new Date(provider.lastInterviewAt).toLocaleString()] }))] }), provider.interviewDetails?.role && (_jsxs("div", { className: "text-[10px] text-text-muted", children: ["\u89D2\u8272: ", _jsx("span", { className: "text-text-main", children: provider.interviewDetails.role })] })), provider.interviewDetails?.runId && (_jsxs("div", { className: "text-[10px] text-text-muted", children: ["\u8FD0\u884CID: ", _jsx("span", { className: "text-text-main font-mono", children: provider.interviewDetails.runId })] }))] })), provider.lastTest && (_jsxs("div", { className: "space-y-3", children: [_jsxs("h5", { className: "text-xs font-semibold text-text-main flex items-center gap-2", children: [_jsx(Clock, { className: "size-3.5 text-cyan-400" }), "\u4E0A\u6B21\u6D4B\u8BD5"] }), _jsxs("div", { className: "space-y-2 text-xs", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u65F6\u95F4:" }), _jsx("span", { className: "text-text-main", children: new Date(provider.lastTest.at).toLocaleString() })] }), provider.lastTest.latencyMs && (_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u5EF6\u8FDF:" }), _jsxs("span", { className: "text-text-main", children: [provider.lastTest.latencyMs, "ms"] })] })), provider.lastTest.usage && (_jsxs("div", { className: "flex justify-between", children: [_jsx("span", { className: "text-text-muted", children: "\u4EE4\u724C:" }), _jsxs("span", { className: "text-text-main", children: [provider.lastTest.usage.totalTokens, " ", provider.lastTest.usage.estimated ? '(est.)' : ''] })] })), provider.lastTest.note && (_jsx("div", { className: "text-text-main", children: provider.lastTest.note }))] })] })), _jsxs("div", { className: "flex items-center gap-2 pt-3 border-t border-white/10", children: [_jsxs("button", { onClick: onTest, disabled: provider.status === 'testing', className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-cyan-400/40 disabled:opacity-60 flex items-center gap-1", children: [_jsx(PlayCircle, { className: "size-3" }), "\u6D4B\u8BD5"] }), isCLIConnection(provider.conn) && cliMode === 'tui' && onOpenTuiBrowser && (_jsxs("button", { onClick: onOpenTuiBrowser, className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-cyan-400/40 flex items-center gap-1", children: [_jsx(Terminal, { className: "size-3" }), "TUI \u6D4F\u89C8\u5668"] })), onViewTestReport && (_jsxs("button", { onClick: onViewTestReport, className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1", children: [_jsx(Eye, { className: "size-3" }), "\u67E5\u770B\u62A5\u544A"] })), _jsxs("button", { onClick: () => setIsEditing(true), className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1", children: [_jsx(Edit3, { className: "size-3" }), "\u7F16\u8F91"] }), _jsxs("button", { onClick: onDelete, className: "px-3 py-1.5 text-[10px] border border-red-500/30 rounded hover:border-red-500/40 text-red-400 flex items-center gap-1", children: [_jsx(Trash2, { className: "size-3" }), "\u5220\u9664"] })] }), isCLIConnection(provider.conn) && provider.conn.command && (_jsxs("div", { className: "space-y-2 text-xs border-t border-white/10 pt-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-muted", children: "\u547D\u4EE4" }), _jsxs("div", { className: "font-mono text-[10px] text-text-main bg-black/30 rounded px-3 py-2 border border-white/10", children: [provider.conn.command, " ", (provider.conn.args || []).join(' ')] })] })), isHTTPConnection(provider.conn) && provider.conn.baseUrl && (_jsxs("div", { className: "space-y-2 text-xs border-t border-white/10 pt-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-muted", children: "\u57FA\u7840URL" }), _jsx("div", { className: "font-mono text-[10px] text-text-main bg-black/30 rounded px-3 py-2 border border-white/10 break-all", children: provider.conn.baseUrl })] }))] }));
    const renderEditView = () => (_jsxs("div", { className: "space-y-4 pt-4 border-t border-white/10", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u7F16\u8F91\u63D0\u4F9B\u5546" }), _jsxs("div", { className: "space-y-3", children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u540D\u79F0" }), _jsx("input", { type: "text", value: editForm.name, onChange: (e) => setEditForm(prev => ({ ...prev, name: e.target.value })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u7C7B\u578B" }), _jsxs("select", { value: editForm.kind, onChange: (e) => {
                                    const newKind = e.target.value;
                                    setEditForm((prev) => {
                                        const baseProvider = { ...prev, kind: newKind, cliMode: isCLIProvider(newKind) ? 'headless' : undefined };
                                        if (newKind === 'codex_cli' || newKind === 'gemini_cli') {
                                            return {
                                                ...baseProvider,
                                                conn: { kind: newKind, command: '', args: [], env: {} }
                                            };
                                        }
                                        else {
                                            return {
                                                ...baseProvider,
                                                conn: { kind: 'http', baseUrl: '' }
                                            };
                                        }
                                    });
                                }, className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm", children: [_jsx("option", { value: "codex_cli", children: "Codex CLI" }), _jsx("option", { value: "gemini_cli", children: "Gemini CLI" }), _jsx("option", { value: "ollama", children: "Ollama" }), _jsx("option", { value: "openai_compat", children: "OpenAI \u517C\u5BB9" }), _jsx("option", { value: "anthropic_compat", children: "Anthropic \u517C\u5BB9" }), _jsx("option", { value: "custom_https", children: "\u81EA\u5B9A\u4E49 HTTPS" })] })] }), isCLIConnection(editForm.conn) ? (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "CLI \u6A21\u5F0F" }), _jsxs("select", { value: editForm.cliMode || 'headless', onChange: (e) => setEditForm(prev => ({ ...prev, cliMode: e.target.value })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm", children: [_jsx("option", { value: "headless", children: "\u9759\u9ED8\u6267\u884C\uFF08\u975E\u4EA4\u4E92\uFF09" }), _jsx("option", { value: "tui", children: "TUI\uFF08\u4EA4\u4E92\uFF09" })] })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u547D\u4EE4" }), _jsx("input", { type: "text", value: editForm.conn.command, onChange: (e) => setEditForm(prev => ({
                                            ...prev,
                                            conn: { ...prev.conn, kind: editForm.conn.kind, command: e.target.value }
                                        })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono", placeholder: "\u4F8B\u5982 codex\u3001gemini" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u53C2\u6570\uFF08\u6BCF\u884C\u4E00\u9879\uFF09" }), _jsx("textarea", { value: (editForm.conn.args || []).join('\n'), onChange: (e) => setEditForm(prev => ({
                                            ...prev,
                                            conn: { ...prev.conn, kind: editForm.conn.kind, args: e.target.value.split('\n').filter(Boolean) }
                                        })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16" })] }), usesOutputPath && (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u8F93\u51FA\u8DEF\u5F84\uFF08\u53EF\u9009\uFF09" }), _jsx("input", { type: "text", value: editForm.outputPath || "", onChange: (e) => setEditForm(prev => ({ ...prev, outputPath: e.target.value })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono", placeholder: "runtime/CODEX_LAST_MESSAGE.md" }), _jsxs("p", { className: "text-[9px] text-text-dim mt-1", children: ["\u4EC5\u5F53 args \u4E2D\u5305\u542B ", `{output}`, " \u65F6\u624D\u4F1A\u5199\u5165\u3002"] })] })), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { type: "button", onClick: applyCodexPreset, className: "px-3 py-1.5 text-[10px] border border-emerald-500/30 rounded hover:border-emerald-400/60 text-emerald-200", children: "\u5E94\u7528 Codex CLI \u9884\u8BBE" }), _jsx("span", { className: "text-[9px] text-text-dim", children: "\u63A8\u8350\u7528\u4E8E codex exec + \u9762\u8BD5\u6D4B\u8BD5" })] })] })) : (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u57FA\u7840 URL" }), _jsx("input", { type: "text", value: isHTTPConnection(editForm.conn) ? editForm.conn.baseUrl : '', onChange: (e) => setEditForm(prev => ({
                                            ...prev,
                                            conn: { ...prev.conn, kind: 'http', baseUrl: e.target.value }
                                        })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono", placeholder: "https://api.example.com/v1" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u5BC6\u94A5" }), _jsx("input", { type: "text", value: isHTTPConnection(editForm.conn) ? editForm.conn.apiKey || '' : '', onChange: (e) => setEditForm(prev => ({
                                            ...prev,
                                            conn: { ...prev.conn, kind: 'http', apiKey: e.target.value }
                                        })), className: "w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono", placeholder: "\u8BF7\u8F93\u5165 API \u5BC6\u94A5" })] })] })), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6A21\u578B ID" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { type: "text", value: editForm.modelId, onChange: (e) => setEditForm(prev => ({ ...prev, modelId: e.target.value })), className: "flex-1 bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono", placeholder: "\u4F8B\u5982 gpt-4\u3001claude-3-5-sonnet" }), renderModelBrowser
                                        ? renderModelBrowser({
                                            modelId: editForm.modelId,
                                            onSelect: (value) => setEditForm((prev) => ({ ...prev, modelId: value })),
                                        })
                                        : null] })] })] }), _jsxs("div", { className: "flex items-center gap-2 pt-3 border-t border-white/10", children: [_jsx("button", { onClick: handleSaveEdit, className: "px-3 py-1.5 text-[10px] font-semibold bg-accent/80 hover:bg-accent text-white rounded transition-colors", children: "\u4FDD\u5B58" }), _jsx("button", { onClick: handleCancelEdit, className: "px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40", children: "\u53D6\u6D88" })] })] }));
    return (_jsxs("div", { className: "bg-white/5 rounded-xl p-4 border border-white/10 hover:border-white/20 transition-all", children: [_jsx("div", { className: "flex items-center justify-between mb-2", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${STATUS_BADGES[provider.status]}`, children: provider.status.toUpperCase() }), _jsx("span", { className: "text-[10px] text-text-dim capitalize", children: PROVIDER_LABELS[provider.kind] }), isCodexCli && (_jsx("span", { className: "px-2 py-1 text-[9px] uppercase font-semibold rounded border bg-emerald-500/10 text-emerald-200 border-emerald-500/30", children: "Codex CLI" }))] }) }), isEditing ? renderEditView() : (_jsxs(_Fragment, { children: [renderCompactView(), isExpanded && renderExpandedView()] }))] }));
}
