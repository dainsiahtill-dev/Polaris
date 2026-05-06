/**
 * ConversationList - Conversation list
 *
 * Features:
 * - List of conversations
 * - Create new conversation button
 * - Delete conversation button
 * - Click to open
 */

import { useState, useCallback, useEffect } from 'react';
import { useConversations } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';
import type { ConversationV2, CreateConversationRequestV2 } from '@/services/api.types';

export interface ConversationListProps {
  role?: string;
  workspace?: string;
  onSelect?: (conversation: ConversationV2) => void;
  onDelete?: (conversationId: string) => void;
}

export function ConversationList({
  role,
  workspace,
  onSelect,
  onDelete,
}: ConversationListProps): JSX.Element {
  const {
    conversations,
    total,
    loading,
    error,
    list,
    create,
  } = useConversations();
  const { apiError } = useV2ApiError();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newRole, setNewRole] = useState(role || 'pm');

  useEffect(() => {
    void list({ role, workspace, limit: 50 });
  }, [list, role, workspace]);

  const handleRefresh = useCallback(() => {
    void list({ role, workspace, limit: 50 });
  }, [list, role, workspace]);

  const handleCreate = useCallback(async () => {
    const request: CreateConversationRequestV2 = {
      title: newTitle.trim() || undefined,
      role: newRole,
      workspace: workspace || undefined,
    };
    const created = await create(request);
    if (created) {
      setNewTitle('');
      setShowCreateForm(false);
      void list({ role, workspace, limit: 50 });
    }
  }, [create, newTitle, newRole, workspace, list, role]);

  const handleDelete = useCallback(
    (conversationId: string) => {
      onDelete?.(conversationId);
    },
    [onDelete]
  );

  const handleSelect = useCallback(
    (conversation: ConversationV2) => {
      onSelect?.(conversation);
    },
    [onSelect]
  );

  return (
    <div className="flex flex-col h-full border rounded-lg bg-white dark:bg-gray-900">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Conversations
          {total > 0 && (
            <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">
              ({total})
            </span>
          )}
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            onClick={() => setShowCreateForm((prev) => !prev)}
            className="px-3 py-1 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 transition-colors"
          >
            New
          </button>
        </div>
      </div>

      {showCreateForm && (
        <div className="px-4 py-3 border-b bg-gray-50 dark:bg-gray-800/50">
          <div className="space-y-2">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Title
              </label>
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Conversation title (optional)"
                className="w-full text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Role
              </label>
              <select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value)}
                className="w-full text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="pm">PM</option>
                <option value="architect">Architect</option>
                <option value="chief_engineer">Chief Engineer</option>
                <option value="director">Director</option>
                <option value="qa">QA</option>
                <option value="scout">Scout</option>
              </select>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => void handleCreate()}
                disabled={loading}
                className="px-3 py-1 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                Create
              </button>
              <button
                onClick={() => setShowCreateForm(false)}
                className="px-3 py-1 text-xs font-medium text-gray-700 bg-gray-100 dark:bg-gray-700 dark:text-gray-300 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto min-h-0">
        {loading && conversations.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full mr-2" />
            <span className="text-sm text-gray-500 dark:text-gray-400">
              Loading...
            </span>
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <div className="text-center text-sm text-gray-400 dark:text-gray-600 py-8">
            No conversations found.
          </div>
        )}

        <ul className="divide-y">
          {conversations.map((conversation) => (
            <li
              key={conversation.id}
              className="px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-pointer"
              onClick={() => handleSelect(conversation)}
            >
              <div className="flex items-center justify-between">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                    {conversation.title || `Conversation ${conversation.id.slice(0, 8)}`}
                  </p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="inline-flex px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                      {conversation.role}
                    </span>
                    {conversation.message_count > 0 && (
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {conversation.message_count} message(s)
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(conversation.id);
                  }}
                  className="ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                  aria-label={`Delete conversation ${conversation.id}`}
                >
                  Delete
                </button>
              </div>
              {conversation.updated_at && (
                <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                  {new Date(conversation.updated_at).toLocaleString()}
                </p>
              )}
            </li>
          ))}
        </ul>
      </div>

      {(error || apiError.hasError) && (
        <div className="border-t px-4 py-3 text-xs text-red-600 dark:text-red-400">
          {error || apiError.error?.message}
        </div>
      )}
    </div>
  );
}
