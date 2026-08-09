import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
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
import { useState, useCallback, useEffect } from 'react';
import { useRoleChat } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';
import { roleChatRolesService } from '@/services/api';
const ROLE_LABELS = {
    pm: 'PM (尚书令)',
    architect: 'Architect (中书令)',
    chief_engineer: 'Chief Engineer (工部尚书)',
    director: 'Director (工部侍郎)',
    qa: 'QA (门下侍中)',
    resident_agi: 'Resident AGI (平台总控)',
};
const FALLBACK_ROLE_OPTIONS = [
    { value: 'pm', label: 'PM (尚书令)' },
    { value: 'architect', label: 'Architect (中书令)' },
    { value: 'chief_engineer', label: 'Chief Engineer (工部尚书)' },
    { value: 'director', label: 'Director (工部侍郎)' },
    { value: 'qa', label: 'QA (门下侍中)' },
    { value: 'resident_agi', label: 'Resident AGI (平台总控)' },
], satisfies, RoleOption, [];
function isRoleChatRole(value) {
    return Object.prototype.hasOwnProperty.call(ROLE_LABELS, value);
}
function roleOptionsFromRegistry(roles) {
    const seen = new Set();
    const options = [];
    for (const role of roles) {
        if (!isRoleChatRole(role) || seen.has(role))
            continue;
        seen.add(role);
        options.push({ value: role, label: ROLE_LABELS[role] });
    }
    return options;
}
export function RoleChatPanel({ defaultRole = 'pm', onError }) {
    const [selectedRole, setSelectedRole] = useState(defaultRole);
    const [roleOptions, setRoleOptions] = useState(FALLBACK_ROLE_OPTIONS);
    const [roleRegistrySource, setRoleRegistrySource] = useState('loading');
    const [message, setMessage] = useState('');
    const { response, thinking, loading, error, sendMessage, reset } = useRoleChat(selectedRole);
    const { apiError } = useV2ApiError();
    useEffect(() => {
        let cancelled = false;
        async function loadRoleRegistry() {
            const result = await roleChatRolesService.list();
            if (cancelled)
                return;
            if (result.ok && result.data) {
                const backendOptions = roleOptionsFromRegistry(result.data.roles);
                if (backendOptions.length > 0) {
                    setRoleOptions(backendOptions);
                    setRoleRegistrySource('backend');
                    setSelectedRole((current) => {
                        if (backendOptions.some((role) => role.value === current))
                            return current;
                        reset();
                        return backendOptions[0].value;
                    });
                    return;
                }
            }
            setRoleOptions(FALLBACK_ROLE_OPTIONS);
            setRoleRegistrySource('fallback');
            setSelectedRole((current) => {
                if (FALLBACK_ROLE_OPTIONS.some((role) => role.value === current))
                    return current;
                reset();
                return 'pm';
            });
        }
        void loadRoleRegistry().catch(() => {
            if (cancelled)
                return;
            setRoleOptions(FALLBACK_ROLE_OPTIONS);
            setRoleRegistrySource('fallback');
        });
        return () => {
            cancelled = true;
        };
    }, [reset]);
    const handleSend = useCallback(async () => {
        if (!message.trim() || loading)
            return;
        const msg = message.trim();
        setMessage('');
        try {
            await sendMessage(msg);
        }
        catch (err) {
            const errorMsg = err instanceof Error ? err.message : String(err);
            apiError.setError({ code: 'ROLE_CHAT_ERROR', message: errorMsg, status: 500 });
            onError?.(errorMsg);
        }
    }, [message, loading, sendMessage, apiError, onError]);
    const handleKeyDown = useCallback((e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void handleSend();
        }
    }, [handleSend]);
    const handleRoleChange = useCallback((e) => {
        if (!isRoleChatRole(e.target.value))
            return;
        setSelectedRole(e.target.value);
        reset();
    }, [reset]);
    return (_jsxs("div", { className: "flex flex-col h-full border rounded-lg bg-white dark:bg-gray-900", children: [_jsxs("div", { className: "flex items-center justify-between px-4 py-3 border-b", children: [_jsx("h2", { className: "text-sm font-semibold text-gray-900 dark:text-gray-100", children: "Role Chat" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("span", { "data-testid": "role-chat-registry-source", className: "text-[11px] text-gray-500 dark:text-gray-400", children: ["roles: ", roleRegistrySource] }), _jsx("select", { value: selectedRole, onChange: handleRoleChange, className: "text-sm border rounded px-2 py-1 bg-white dark:bg-gray-800 dark:text-gray-100", "aria-label": "Select role", children: roleOptions.map((role) => (_jsx("option", { value: role.value, children: role.label }, role.value))) })] })] }), _jsxs("div", { className: "flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0", children: [response && (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "bg-blue-50 dark:bg-blue-900/20 rounded-lg p-3", children: [_jsx("p", { className: "text-xs font-medium text-blue-700 dark:text-blue-300 mb-1", children: "Response" }), _jsx("p", { className: "text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap", children: response })] }), thinking && (_jsxs("div", { className: "bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3", children: [_jsx("p", { className: "text-xs font-medium text-gray-500 dark:text-gray-400 mb-1", children: "Thinking" }), _jsx("p", { className: "text-sm text-gray-600 dark:text-gray-400 whitespace-pre-wrap", children: thinking })] }))] })), !response && !loading && (_jsx("div", { className: "text-center text-gray-400 dark:text-gray-600 text-sm py-8", children: "Select a role and send a message to start chatting" })), loading && (_jsxs("div", { className: "flex items-center justify-center py-8", children: [_jsx("div", { className: "animate-spin h-5 w-5 border-2 border-blue-500 border-t-transparent rounded-full mr-2" }), _jsx("span", { className: "text-sm text-gray-500 dark:text-gray-400", children: "Waiting for response..." })] })), (error || apiError.hasError) && (_jsxs("div", { className: "bg-red-50 dark:bg-red-900/20 rounded-lg p-3", children: [_jsx("p", { className: "text-xs font-medium text-red-700 dark:text-red-300 mb-1", children: "Error" }), _jsx("p", { className: "text-sm text-red-600 dark:text-red-400", children: error || apiError.error?.message })] }))] }), _jsxs("div", { className: "px-4 py-3 border-t", children: [_jsxs("div", { className: "flex gap-2", children: [_jsx("textarea", { value: message, onChange: (e) => setMessage(e.target.value), onKeyDown: handleKeyDown, placeholder: `Message ${selectedRole}...`, rows: 2, className: "flex-1 text-sm border rounded-lg px-3 py-2 resize-none bg-white dark:bg-gray-800 dark:text-gray-100 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500", disabled: loading, "aria-label": "Message input" }), _jsx("button", { onClick: () => void handleSend(), disabled: !message.trim() || loading, className: "px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors", "aria-label": "Send message", children: "Send" })] }), _jsx("p", { className: "text-xs text-gray-400 mt-1", children: "Ctrl+Enter to send" })] })] }));
}
