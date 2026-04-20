/**
 * AI 对话组件库统一导出
 */

// 主组件
export { AIDialoguePanel } from './AIDialoguePanel';
export type { AIDialoguePanelProps, DialogueRole } from './AIDialoguePanel';

// 子组件
export { AIDialogueHeader } from './AIDialogueHeader';
export type { AIDialogueHeaderProps } from './AIDialogueHeader';

export { AIMessageList } from './AIMessageList';
export type { AIMessageListProps, AIMessage } from './AIMessageList';

export { AIInputArea } from './AIInputArea';
export type { AIInputAreaProps } from './AIInputArea';

export { AIStatusBar, AIHistoryPanel } from './AIStatusBar';
export type { AIStatusBarProps, AIHistoryPanelProps, ConversationItem } from './AIStatusBar';

// Hooks
export { useRoleChat } from './useRoleChat';
export type {
  Message,
  UseRoleChatOptions,
  UseRoleChatReturn,
} from './useRoleChat';

export { useChatStream } from './useChatStream';
export type {
  ChatStreamMessage,
  ChatStreamOptions,
  ChatStreamReturn,
} from './useChatStream';

export { useAIDialogue } from './useAIDialogue';
export type {
  UseAIDialogueOptions,
  UseAIDialogueReturn,
} from './useAIDialogue';

// 状态相关
export { resolveDialogueStatusKind } from './chatStatusState';
export type { DialogueChatStatus } from './chatStatusState';

// 类型
export type { ChatStatus } from '@/services';

// 状态指示器
export { ManusStyleStatusIndicator, MiniStatusBadge } from './ManusStyleStatusIndicator';
export type { StatusPhase } from './ManusStyleStatusIndicator';
