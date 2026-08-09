import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useMemo, useState } from 'react';
import { MessageSquare, FileText, Brain, Database, Camera, Bot } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { DialoguePanel } from '@/app/components/DialoguePanel';
import { MemoPanel } from '@/app/components/MemoPanel';
import { MemoryPanel } from '@/app/components/MemoryPanel';
import { CognitionPanel } from '@/app/components/CognitionPanel';
import { SnapshotPanel } from '@/app/components/SnapshotPanel';
export function ContextSidebar({ dialogueEvents, runtimeEvents = [], live, dialogueLoading, onClearDialogueLogs, clearingDialogueLogs = false, memoItems, memoSelected, memoContent, memoMtime, memoLoading, memoError, onSelectMemo, memoryContent, memoryMtime, memoryLoading, memoryError, showCognition, setShowCognition, settingsShowMemory, anthroState, snapshotTimestamp, snapshotFileStatus, snapshotFilePaths, snapshotDirectorState, resident, activeTab: controlledActiveTab, onActiveTabChange, }) {
    const [uncontrolledActiveTab, setUncontrolledActiveTab] = useState('dialogue');
    const activeTab = controlledActiveTab ?? uncontrolledActiveTab;
    const setActiveTab = useCallback((tab) => {
        setUncontrolledActiveTab(tab);
        onActiveTabChange?.(tab);
    }, [onActiveTabChange]);
    const visibleDialogueEvents = useMemo(() => mergeDialogueAndRuntimeEvents(dialogueEvents, runtimeEvents), [dialogueEvents, runtimeEvents]);
    return (_jsxs("div", { "data-testid": "context-sidebar", className: "flex h-full glass-bubble border-l-0 overflow-hidden", children: [_jsxs("div", { className: "w-14 flex flex-col items-center py-6 gap-6 border-r border-white/5 bg-black/30 z-20", children: [_jsx(TabButton, { active: activeTab === 'dialogue', onClick: () => setActiveTab('dialogue'), icon: _jsx(MessageSquare, { className: "size-5" }), label: "Discussion", testId: "context-tab-dialogue" }), _jsx(TabButton, { active: activeTab === 'memos', onClick: () => setActiveTab('memos'), icon: _jsx(FileText, { className: "size-5" }), label: "\u5907\u5FD8", testId: "context-tab-memos" }), settingsShowMemory && (_jsx(TabButton, { active: activeTab === 'memory', onClick: () => setActiveTab('memory'), icon: showCognition ? _jsx(Brain, { className: "size-5" }) : _jsx(Database, { className: "size-5" }), label: "\u5FC6\u5E93", testId: "context-tab-memory" })), _jsx(TabButton, { active: activeTab === 'snapshot', onClick: () => setActiveTab('snapshot'), icon: _jsx(Camera, { className: "size-5" }), label: "\u5FEB\u7167", testId: "context-tab-snapshot" }), _jsx(TabButton, { active: activeTab === 'agi', onClick: () => setActiveTab('agi'), icon: _jsx(Bot, { className: "size-5" }), label: "AGI", testId: "context-tab-agi" })] }), _jsx("div", { className: "flex-1 min-w-0 flex flex-col relative bg-gradient-to-br from-transparent to-black/20", children: _jsxs(AnimatePresence, { mode: "wait", children: [activeTab === 'dialogue' && (_jsxs(motion.div, { initial: { opacity: 0, x: 20 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -20 }, transition: { duration: 0.2, ease: "easeOut" }, className: "absolute inset-0 flex flex-col", children: [_jsxs("div", { className: "flex-none p-3 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(MessageSquare, { className: "size-4 text-blue-400" }), _jsx("span", { className: "text-xs font-bold text-text-main uppercase tracking-widest", children: "\u5BF9\u8BDD\u6D41" })] }), _jsx("div", { className: "text-[10px] text-text-dim px-2 py-0.5 rounded-full bg-black/30 border border-white/5", children: live ? 'Active' : '离线' })] }), _jsx("div", { className: "flex-1 min-h-0 relative", children: _jsx(DialoguePanel, { events: visibleDialogueEvents, live: live, loading: dialogueLoading, onClearLogs: onClearDialogueLogs, clearingLogs: clearingDialogueLogs }) })] }, "dialogue")), activeTab === 'memos' && (_jsx(motion.div, { initial: { opacity: 0, x: 20 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -20 }, transition: { duration: 0.2, ease: "easeOut" }, className: "absolute inset-0 flex flex-col", children: _jsx(MemoPanel, { items: memoItems, selected: memoSelected, content: memoContent, mtime: memoMtime, loading: memoLoading, error: memoError, onSelect: onSelectMemo }) }, "memos")), activeTab === 'memory' && settingsShowMemory && (_jsxs(motion.div, { initial: { opacity: 0, x: 20 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -20 }, transition: { duration: 0.2, ease: "easeOut" }, className: "absolute inset-0 flex flex-col", children: [_jsxs("div", { className: "flex-none p-2 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md", children: [_jsxs("div", { className: "flex items-center gap-2", children: [showCognition ? _jsx(Brain, { className: "size-4 text-purple-400" }) : _jsx(Database, { className: "size-4 text-blue-400" }), _jsx("span", { className: "text-xs font-bold text-text-main uppercase tracking-widest", children: "\u5FC6\u5E93" })] }), _jsxs("div", { className: "flex bg-black/30 p-0.5 rounded-lg border border-white/5", children: [_jsx("button", { onClick: () => setShowCognition(true), className: `px-2 py-1 text-[10px] rounded transition-all ${showCognition ? 'bg-white/10 text-slate-200' : 'text-gray-500 hover:text-gray-300'}`, children: "\u8BA4\u77E5" }), _jsx("button", { onClick: () => setShowCognition(false), className: `px-2 py-1 text-[10px] rounded transition-all ${!showCognition ? 'bg-blue-500/20 text-blue-300' : 'text-gray-500 hover:text-gray-300'}`, children: "\u539F\u59CB" })] })] }), _jsx("div", { className: "flex-1 min-h-0 relative overflow-hidden", children: showCognition ? (_jsx(CognitionPanel, { events: dialogueEvents, loading: !live, anthroState: anthroState })) : (_jsx(MemoryPanel, { content: memoryContent, mtime: memoryMtime, loading: memoryLoading, error: memoryError })) })] }, "memory")), activeTab === 'snapshot' && (_jsxs(motion.div, { initial: { opacity: 0, x: 20 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -20 }, transition: { duration: 0.2, ease: "easeOut" }, className: "absolute inset-0 flex flex-col", children: [_jsx("div", { className: "flex-none p-3 border-b border-white/5 flex items-center bg-white/5 backdrop-blur-md", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Camera, { className: "size-4 text-green-400" }), _jsx("span", { className: "text-xs font-bold text-text-main uppercase tracking-widest", children: "Workspace Snapshot" })] }) }), _jsx("div", { className: "flex-1 min-h-0 relative overflow-auto", children: _jsx(SnapshotPanel, { timestamp: snapshotTimestamp, fileStatus: snapshotFileStatus, filePaths: snapshotFilePaths, directorState: snapshotDirectorState }) })] }, "snapshot")), activeTab === 'agi' && (_jsxs(motion.div, { initial: { opacity: 0, x: 20 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -20 }, transition: { duration: 0.2, ease: "easeOut" }, className: "absolute inset-0 flex flex-col", children: [_jsx("div", { className: "flex-none p-3 border-b border-white/5 flex items-center bg-white/5 backdrop-blur-md", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Bot, { className: "size-4 text-cyan-300" }), _jsx("span", { className: "text-xs font-bold text-text-main uppercase tracking-widest", children: "AGI \u6458\u8981" })] }) }), _jsxs("div", { className: "flex-1 overflow-auto p-4 space-y-4", children: [_jsxs("div", { className: "rounded-xl border border-white/10 bg-black/20 p-4", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.24em] text-text-dim", children: "Identity" }), _jsx("div", { className: "mt-2 text-sm font-semibold text-text-main", children: resident?.identity?.name || 'Resident AGI Supervisor' }), _jsx("div", { className: "mt-1 text-xs text-text-dim", children: resident?.identity?.mission || '尚未设定任务宣言' })] }), _jsxs("div", { className: "grid gap-3 sm:grid-cols-2", children: [_jsx(AgiMetric, { label: "Mode", value: resident?.runtime?.mode || resident?.identity?.operating_mode || 'observe' }), _jsx(AgiMetric, { label: "Tick", value: String(resident?.runtime?.tick_count ?? 0) }), _jsx(AgiMetric, { label: "Goals", value: String(resident?.counts?.goals ?? 0) }), _jsx(AgiMetric, { label: "Decisions", value: String(resident?.counts?.decisions ?? 0) })] }), _jsxs("div", { className: "rounded-xl border border-white/10 bg-black/20 p-4", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.24em] text-text-dim", children: "Focus" }), _jsx("div", { className: "mt-2 flex flex-wrap gap-2", children: (resident?.agenda?.current_focus || []).length ? (resident?.agenda?.current_focus?.map((item) => (_jsx("span", { className: "rounded-full border border-cyan-400/20 bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-100", children: item }, item)))) : (_jsx("span", { className: "text-xs text-text-dim", children: "\u6682\u65E0\u7126\u70B9" })) })] }), _jsxs("div", { className: "rounded-xl border border-white/10 bg-black/20 p-4", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.24em] text-text-dim", children: "Risk Register" }), _jsx("div", { className: "mt-2 space-y-2", children: (resident?.agenda?.risk_register || []).length ? (resident?.agenda?.risk_register?.map((item) => (_jsx("div", { className: "rounded-xl border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-100", children: item }, item)))) : (_jsx("span", { className: "text-xs text-text-dim", children: "\u6682\u65E0\u98CE\u9669" })) })] })] })] }, "agi"))] }) })] }));
}
function mergeDialogueAndRuntimeEvents(dialogueEvents, runtimeEvents) {
    const adaptedRuntimeEvents = runtimeEvents
        .map((entry, index) => runtimeEventToDialogueEvent(entry, index))
        .filter((event) => event !== null);
    return [...dialogueEvents, ...adaptedRuntimeEvents].slice(-300);
}
function runtimeEventToDialogueEvent(entry, index) {
    const message = String(entry.message || '').trim();
    const title = String(entry.title || '').trim();
    const details = String(entry.details || '').trim();
    const contentParts = [title, message, details].filter(Boolean);
    if (!contentParts.length)
        return null;
    const meta = entry.meta && typeof entry.meta === 'object' ? entry.meta : {};
    const taskId = firstStringValue(meta, ['task_id', 'taskId', 'project_id', 'projectId']);
    const status = inferRuntimeResultStatus(entry);
    const content = status
        ? `Event receipt: ${status} - ${contentParts.join(' · ')}`
        : contentParts.join(' · ');
    return {
        seq: index,
        eventId: entry.id,
        speaker: inferRuntimeSpeaker(entry),
        type: status ? 'result' : 'event',
        content,
        timestamp: entry.timestamp,
        refs: {
            task_id: taskId || undefined,
            phase: firstStringValue(meta, ['phase', 'stage']) || undefined,
        },
        meta,
    };
}
function firstStringValue(meta, keys) {
    for (const key of keys) {
        const value = meta[key];
        if (typeof value === 'string' && isMeaningfulRuntimeRef(value))
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function isMeaningfulRuntimeRef(value) {
    const token = value.trim().toLowerCase();
    return Boolean(token) && token !== '-' && token !== 'unknown' && token !== 'none' && token !== 'null';
}
function inferRuntimeSpeaker(entry) {
    const source = `${entry.source || ''} ${entry.title || ''} ${entry.message || ''}`.toLowerCase();
    if (source.includes('director'))
        return 'Director';
    if (source.includes('qa') || source.includes('quality'))
        return 'QA';
    if (source.includes('review'))
        return 'Reviewer';
    if (source.includes('pm'))
        return 'PM';
    return 'System';
}
function inferRuntimeResultStatus(entry) {
    const meta = entry.meta && typeof entry.meta === 'object' ? entry.meta : {};
    const okValue = meta.ok;
    if (typeof okValue === 'boolean')
        return okValue ? 'PASS' : 'FAIL';
    const exitCodeValue = meta.exit_code ?? meta.exitCode;
    if (typeof exitCodeValue === 'number' && Number.isFinite(exitCodeValue)) {
        return exitCodeValue === 0 ? 'PASS' : 'FAIL';
    }
    if (typeof exitCodeValue === 'string' && exitCodeValue.trim()) {
        const parsed = Number(exitCodeValue);
        if (Number.isFinite(parsed))
            return parsed === 0 ? 'PASS' : 'FAIL';
    }
    const token = `${entry.level || ''} ${entry.title || ''} ${entry.message || ''}`.toLowerCase();
    const exitMatch = /\bexit(?:_code)?\s*[=:]\s*(-?\d+)\b/.exec(token);
    if (exitMatch) {
        const parsed = Number(exitMatch[1]);
        if (Number.isFinite(parsed))
            return parsed === 0 ? 'PASS' : 'FAIL';
    }
    if (entry.level === 'error' || token.includes('fail') || token.includes('failed'))
        return 'FAIL';
    if (entry.level === 'success' || token.includes('=ok') || token.includes('pass') || token.includes('completed')) {
        return 'PASS';
    }
    return '';
}
function AgiMetric({ label, value }) {
    return (_jsxs("div", { className: "rounded-xl border border-white/10 bg-black/20 p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-[0.24em] text-text-dim", children: label }), _jsx("div", { className: "mt-2 text-sm font-semibold text-text-main", children: value })] }));
}
function TabButton({ active, onClick, icon, label, testId }) {
    return (_jsxs("button", { onClick: onClick, "data-testid": testId, className: `group relative flex flex-col items-center justify-center p-3 rounded-xl transition-all duration-200 ${active ? 'bg-white/10 text-accent border border-white/10' : 'text-text-muted hover:text-white hover:bg-white/5'}`, title: label, children: [active && (_jsx(motion.div, { layoutId: "activeTabIndicator", className: "absolute -left-1 w-1 h-8 bg-accent rounded-r" })), _jsx("div", { className: `transition-all duration-200 ${active ? 'scale-105' : ''}`, children: icon })] }));
}
