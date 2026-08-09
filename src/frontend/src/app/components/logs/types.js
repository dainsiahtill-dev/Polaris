/**
 * Unified Log Pipeline Types
 *
 * Type definitions for the CanonicalLogEventV2 schema and related types.
 * These types mirror the backend CanonicalLogEventV2 model.
 */
// Channel metadata configuration
export const CHANNEL_METADATA = {
    system: {
        id: 'system',
        label: '系统',
        description: '系统事件（运行时、引擎状态、PM报告）',
        icon: 'Cpu',
        color: 'blue',
    },
    process: {
        id: 'process',
        label: '进程',
        description: '进程输出（子进程 stdout/stderr）',
        icon: 'Terminal',
        color: 'green',
    },
    llm: {
        id: 'llm',
        label: 'LLM',
        description: 'LLM 交互事件',
        icon: 'Brain',
        color: 'purple',
    },
};
// Severity styling
export const SEVERITY_STYLES = {
    debug: { bg: 'bg-gray-500/20', text: 'text-gray-400', label: '调试' },
    info: { bg: 'bg-blue-500/20', text: 'text-blue-400', label: '信息' },
    warn: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', label: '警告' },
    error: { bg: 'bg-red-500/20', text: 'text-red-400', label: '错误' },
    critical: { bg: 'bg-red-600/30', text: 'text-red-300', label: '严重' },
};
// Kind styling
export const KIND_STYLES = {
    state: { bg: 'bg-gray-500/20', text: 'text-gray-400', label: '状态' },
    action: { bg: 'bg-amber-500/20', text: 'text-amber-400', label: '动作' },
    observation: { bg: 'bg-emerald-500/20', text: 'text-emerald-400', label: '观察' },
    output: { bg: 'bg-cyan-500/20', text: 'text-cyan-400', label: '输出' },
    error: { bg: 'bg-red-500/20', text: 'text-red-400', label: '错误' },
};
