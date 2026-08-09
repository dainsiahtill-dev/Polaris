/**
 * useRuntimeConnection - 连接状态管理 Hook
 *
 * 职责:
 * - 管理 WebSocket 连接状态
 * - 提供连接控制方法 (connect, disconnect, reconnect)
 * - 订阅角色通道
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRuntimeStore } from "./useRuntimeStore";
import { useConnectionState, useMessageHandler, useTransportActions } from "@/runtime/transport";
import { useSettings } from "@/hooks";
const DEFAULT_RUNTIME_ROLES = [
    "pm",
    "chief_engineer",
    "director",
    "qa",
    "resident_agi",
];
const BASE_RUNTIME_STREAM_CHANNELS = [
    "system",
    "process",
    "llm",
    "dialogue",
    "status.workflow",
    "status.process",
    "status.control_plane",
    "status.resident",
    "status.snapshot",
    "event.factory",
    "event.file_edit",
];
const INTERNAL_BENCH_CHANNEL = "event.bench";
const DEFAULT_RUNTIME_HISTORY_TAIL_LINES = 0;
function normalizeTailLines(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
        return DEFAULT_RUNTIME_HISTORY_TAIL_LINES;
    }
    return Math.max(0, Math.floor(value));
}
function runtimeStreamChannels(includeInternalBench = false) {
    const channels = [...BASE_RUNTIME_STREAM_CHANNELS];
    if (includeInternalBench) {
        channels.splice(channels.length - 1, 0, INTERNAL_BENCH_CHANNEL);
    }
    return channels;
}
function normalizeRoles(input) {
    return Array.from(new Set(input)).sort();
}
function areRolesEqual(left, right) {
    if (left.length !== right.length) {
        return false;
    }
    return left.every((role, index) => role === right[index]);
}
/**
 * useRuntimeConnection - 管理运行时连接状态
 *
 * 代理到 RuntimeTransportProvider split contexts，同时同步状态到 store
 */
export function useRuntimeConnection(options = {}) {
    const { roles = DEFAULT_RUNTIME_ROLES, autoConnect = true, workspace: workspaceProp, includeInternalBench = false, tailLines, } = options;
    const isWorkspaceControlled = workspaceProp !== undefined;
    const { settings, load: loadRuntimeSettings } = useSettings({
        autoLoad: !isWorkspaceControlled,
    });
    const workspace = workspaceProp ?? settings?.workspace ?? "";
    const normalizedRoles = useMemo(() => normalizeRoles(roles), [roles]);
    const normalizedRolesSignature = useMemo(() => normalizedRoles.join("|"), [normalizedRoles]);
    const historyTailLines = normalizeTailLines(tailLines);
    const [subscriptionRoles, setSubscriptionRoles] = useState(normalizedRoles);
    const subscriptionRolesSignature = useMemo(() => subscriptionRoles.join("|"), [subscriptionRoles]);
    // Store state is a compatibility cache for legacy selectors. The transport
    // context remains the authoritative source for live connection state.
    const setConnectionState = useRuntimeStore((s) => s.setConnectionState);
    const resetForWorkspace = useRuntimeStore((s) => s.resetForWorkspace);
    // Transport
    const { connected: transportConnected, reconnecting: transportReconnecting, error: transportError, attemptCount: transportAttemptCount, } = useConnectionState();
    const { subscribeChannels, sendCommand, getLastCursor, reconnect: transportReconnect, } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    // Refs
    const activeRef = useRef(true);
    const rolesRef = useRef(subscriptionRoles);
    const workspaceRef = useRef(workspace);
    const propRolesSignatureRef = useRef(normalizedRolesSignature);
    // Sync connection state to store
    useEffect(() => {
        setConnectionState({
            live: transportConnected,
            error: transportError,
            reconnecting: transportReconnecting,
            attemptCount: transportAttemptCount,
        });
    }, [
        transportConnected,
        transportError,
        transportReconnecting,
        transportAttemptCount,
        setConnectionState,
    ]);
    // Subscribe to concrete runtime channels. Roles are sent as metadata on
    // SUBSCRIBE; using a roles:* pseudo-channel would not match v2 log subjects.
    useEffect(() => {
        const channels = runtimeStreamChannels(includeInternalBench);
        const unsubscribe = subscribeChannels(channels.map((channel) => ({
            channel,
            tailLines: historyTailLines,
        })), rolesRef.current);
        return () => {
            unsubscribe();
        };
    }, [historyTailLines, includeInternalBench, subscribeChannels]);
    // Connect action
    const connect = useCallback((forceRefresh = true) => {
        activeRef.current = true;
        if (forceRefresh) {
            transportReconnect();
        }
    }, [transportReconnect]);
    // Disconnect action
    const disconnect = useCallback(() => {
        activeRef.current = false;
    }, []);
    // Reconnect action
    const reconnect = useCallback(() => {
        transportReconnect();
    }, [transportReconnect]);
    // Update subscription
    const updateSubscription = useCallback((nextRoles) => {
        const normalizedNextRoles = normalizeRoles(nextRoles);
        rolesRef.current = normalizedNextRoles;
        setSubscriptionRoles((previous) => {
            if (areRolesEqual(previous, normalizedNextRoles)) {
                return previous;
            }
            return normalizedNextRoles;
        });
        sendCommand({
            type: "SUBSCRIBE",
            protocol: "runtime.v2",
            roles: normalizedNextRoles,
            tail: historyTailLines,
            channels: runtimeStreamChannels(includeInternalBench),
            cursor: getLastCursor(),
        });
    }, [historyTailLines, includeInternalBench, sendCommand, getLastCursor]);
    // Keep effective subscription roles in sync with prop changes.
    useEffect(() => {
        if (normalizedRolesSignature === propRolesSignatureRef.current) {
            return;
        }
        propRolesSignatureRef.current = normalizedRolesSignature;
        updateSubscription(normalizedRoles);
    }, [normalizedRolesSignature, normalizedRoles, updateSubscription]);
    // Sync refs with effective roles
    useEffect(() => {
        rolesRef.current = subscriptionRoles;
    }, [subscriptionRolesSignature, subscriptionRoles]);
    // Reset state ONLY on workspace change (not on every connection state flip).
    // The previous version depended on transportConnected/transportReconnecting,
    // which caused an infinite loop: connected=true → effect re-runs → reconnect()
    // → disconnect → reconnect → connected=true → effect re-runs → ...
    const prevWorkspaceRef = useRef(workspace);
    const hasInitializedWorkspaceRef = useRef(Boolean(workspace));
    useEffect(() => {
        if (!workspace)
            return;
        if (!hasInitializedWorkspaceRef.current) {
            hasInitializedWorkspaceRef.current = true;
            prevWorkspaceRef.current = workspace;
            return;
        }
        if (workspace === prevWorkspaceRef.current)
            return;
        prevWorkspaceRef.current = workspace;
        resetForWorkspace();
        if (autoConnect && activeRef.current) {
            transportReconnect();
        }
    }, [workspace]);
    // Initial activation
    useEffect(() => {
        activeRef.current = true;
        return () => {
            activeRef.current = false;
        };
    }, []);
    return useMemo(() => ({
        // State
        live: transportConnected,
        connected: transportConnected,
        isConnected: transportConnected,
        error: transportError,
        reconnecting: transportReconnecting,
        attemptCount: transportAttemptCount,
        // Actions
        connect,
        disconnect,
        reconnect,
        updateSubscription,
        // Transport
        transportConnected,
        transportReconnecting,
        transportError,
        transportAttemptCount,
        transportReconnect,
        registerMessageHandler,
        sendCommand,
        // Refs for message handler
        workspaceRef,
        rolesRef,
        activeRef,
    }), [
        connect,
        disconnect,
        reconnect,
        updateSubscription,
        transportConnected,
        transportReconnecting,
        transportError,
        transportAttemptCount,
        transportReconnect,
        registerMessageHandler,
        sendCommand,
    ]);
}
