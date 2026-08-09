/**
 * AI 对话组件库统一导出
 */
// 主组件
export { AIDialoguePanel } from './AIDialoguePanel';
// 子组件
export { AIDialogueHeader } from './AIDialogueHeader';
export { AIMessageList } from './AIMessageList';
export { AIInputArea } from './AIInputArea';
export { AIStatusBar, AIHistoryPanel } from './AIStatusBar';
// Hooks
export { useRoleChat } from './useRoleChat';
export { useAIDialogue } from './useAIDialogue';
// 状态相关
export { resolveDialogueStatusKind } from './chatStatusState';
// 状态指示器
export { ManusStyleStatusIndicator, MiniStatusBadge } from './ManusStyleStatusIndicator';
