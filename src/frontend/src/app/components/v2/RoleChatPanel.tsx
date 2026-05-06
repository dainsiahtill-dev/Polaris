/**
 * RoleChatPanel - Panel for role chat
 *
 * Features:
 * - Input field for message
 * - Send button
 * - Display response
 * - Role selector dropdown
 * - Loading state
 */

import { useState, useCallback } from 'react';
import { useRoleChat } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';

const AVAILABLE_ROLES = [
  { value: 'pm', label: 'PM (尚书令)' },
  { value: 'architect', label: 'Architect (中书令)' },
  { value: 'chief_engineer', label: 'Chief Engineer (工部尚书)' },
  { value: 'director', label: 'Director (工部侍郎)' },
  { value: 'qa', label: 'QA (门下侍中)' },
  { value: 'scout', label: 'Scout (探子)' },
] as const;

export interface RoleChatPanelProps {
  defaultRole?: string;
  onError?: (error: string) => void;
}

export function RoleChatPanel({ defaultRole = 'pm', onError }: RoleChatPanelProps): JSX.Element {
  const [selectedRole, setSelectedRole] = useState<string>(defaultRole);
  const [message, setMessage] = useState('');
  const { response, thinking, loading, error, sendMessage, reset } = useRoleChat(selectedRole);
  const { apiError } = useV2ApiError();

  const handleSend = useCallback(async () => {
    if (!message.trim() || loading) return;
    const msg = message.trim();
    setMessage('');
    try {
      await sendMessage(msg);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      apiError.setError({ code: 'ROLE_CHAT_ERROR', message: errorMsg, status: 500 });
      onError?.(errorMsg);
    }
  }, [message, loading, sendMessage, apiError, onError]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        void handleSend();
      }
    },
    [handleSend]
  );

  const handleRoleChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      setSelectedRole(e.target.value);
      reset();
    },
    [reset]
  );

  return (
    <div className="flex flex-col h-full border rounded-lg bg-white dark:bg-gray-900">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Role Chat</h2>
        <select
          value={selectedRole}
          onChange={handleRoleChange}
          className="text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100"
          aria-label="Select role"
        >
          {AVAILABLE_ROLES.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0">
        {response && (
          <div className="space-y-2">
            <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-3">
              <p className="text-xs font-medium text-blue-700 dark:text-blue-300 mb-1">Response</p>
              <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{response}</p>
            </div>
            {thinking && (
              <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Thinking</p>
                <p className="text-sm text-gray-600 dark:text-gray-400 whitespace-pre-wrap">{thinking}</p>
              </div>
            )}
          </div>
        )}
        {!response && !loading && (
          <div className="text-center text-gray-400 dark:text-gray-600 text-sm py-8">
            Select a role and send a message to start chatting
          </div>
        )}
        {loading && (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full mr-2" />
            <span className="text-sm text-gray-500 dark:text-gray-400">Waiting for response...</span>
          </div>
        )}
        {(error || apiError.hasError) && (
          <div className="bg-red-50 dark:bg-red-900/20 rounded-lg p-3">
            <p className="text-xs font-medium text-red-700 dark:text-red-300 mb-1">Error</p>
            <p className="text-sm text-red-600 dark:text-red-400">{error || apiError.error?.message}</p>
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-t">
        <div className="flex gap-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${selectedRole}...`}
            rows={2}
            className="flex-1 text-sm border rounded-lg px-3 py-2 resize-none bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
            aria-label="Message input"
          />
          <button
            onClick={() => void handleSend()}
            disabled={!message.trim() || loading}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            aria-label="Send message"
          >
            Send
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">Ctrl+Enter to send</p>
      </div>
    </div>
  );
}
