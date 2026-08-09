import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
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
export function ConversationList({ role, workspace, onSelect, onDelete, }) {
    const { conversations, total, loading, error, list, create, } = useConversations();
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
        const request = {
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
    const handleDelete = useCallback((conversationId) => {
        onDelete?.(conversationId);
    }, [onDelete]);
    const handleSelect = useCallback((conversation) => {
        onSelect?.(conversation);
    }, [onSelect]);
    return (_jsxs("div", { className: "flex flex-col h-full border rounded-lg bg-white dark:bg-gray-900", children: [_jsxs("div", { className: "flex items-center justify-between px-4 py-3 border-b", children: [_jsxs("h2", { className: "text-sm font-semibold text-gray-900 dark:text-gray-100", children: ["Conversations", total > 0 && (_jsxs("span", { className: "ml-2 text-xs text-gray-500 dark:text-gray-400", children: ["(", total, ")"] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { onClick: handleRefresh, disabled: loading, className: "text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50", children: "Refresh" }), _jsx("button", { onClick: () => setShowCreateForm((prev) => !prev), className: "px-3 py-1 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 transition-colors", children: "New" })] })] }), showCreateForm && (_jsx("div", { className: "px-4 py-3 border-b bg-gray-50 dark:bg-gray-800/50", children: _jsxs("div", { className: "space-y-2", children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1", children: "Title" }), _jsx("input", { type: "text", value: newTitle, onChange: (e) => setNewTitle(e.target.value), placeholder: "Conversation title (optional)", className: "w-full text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1", children: "Role" }), _jsxs("select", { value: newRole, onChange: (e) => setNewRole(e.target.value), className: "w-full text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500", children: [_jsx("option", { value: "pm", children: "PM" }), _jsx("option", { value: "architect", children: "Architect" }), _jsx("option", { value: "chief_engineer", children: "Chief Engineer" }), _jsx("option", { value: "director", children: "Director" }), _jsx("option", { value: "qa", children: "QA" }), _jsx("option", { value: "scout", children: "Scout" })] })] }), _jsxs("div", { className: "flex gap-2 pt-1", children: [_jsx("button", { onClick: () => void handleCreate(), disabled: loading, className: "px-3 py-1 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 transition-colors", children: "Create" }), _jsx("button", { onClick: () => setShowCreateForm(false), className: "px-3 py-1 text-xs font-medium text-gray-700 bg-gray-100 dark:bg-gray-700 dark:text-gray-300 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors", children: "Cancel" })] })] }) })), _jsxs("div", { className: "flex-1 overflow-y-auto min-h-0", children: [loading && conversations.length === 0 && (_jsxs("div", { className: "flex items-center justify-center py-8", children: [_jsx("div", { className: "animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full mr-2" }), _jsx("span", { className: "text-sm text-gray-500 dark:text-gray-400", children: "Loading..." })] })), !loading && conversations.length === 0 && (_jsx("div", { className: "text-center text-sm text-gray-400 dark:text-gray-600 py-8", children: "No conversations found." })), _jsx("ul", { className: "divide-y", children: conversations.map((conversation) => (_jsxs("li", { className: "px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-pointer", onClick: () => handleSelect(conversation), children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("p", { className: "text-sm font-medium text-gray-900 dark:text-gray-100 truncate", children: conversation.title || `Conversation ${conversation.id.slice(0, 8)}` }), _jsxs("div", { className: "flex items-center gap-2 mt-0.5", children: [_jsx("span", { className: "inline-flex px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400", children: conversation.role }), conversation.message_count > 0 && (_jsxs("span", { className: "text-xs text-gray-500 dark:text-gray-400", children: [conversation.message_count, " message(s)"] }))] })] }), _jsx("button", { onClick: (e) => {
                                                e.stopPropagation();
                                                handleDelete(conversation.id);
                                            }, className: "ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors", "aria-label": `Delete conversation ${conversation.id}`, children: "Delete" })] }), conversation.updated_at && (_jsx("p", { className: "text-xs text-gray-400 dark:text-gray-500 mt-1", children: new Date(conversation.updated_at).toLocaleString() }))] }, conversation.id))) })] }), (error || apiError.hasError) && (_jsx("div", { className: "border-t px-4 py-3 text-xs text-red-600 dark:text-red-400", children: error || apiError.error?.message }))] }));
}
