export interface DialogueChatStatus {
  ready: boolean;
  configured?: boolean;
}

export type DialogueStatusKind = 'loading' | 'ready' | 'unconfigured' | 'error';

export function resolveDialogueStatusKind(
  status: DialogueChatStatus | null,
  statusLoading: boolean
): DialogueStatusKind {
  if (statusLoading) {
    return 'loading';
  }

  if (status?.ready) {
    return 'ready';
  }

  if (status?.configured === false) {
    return 'unconfigured';
  }

  return 'error';
}
