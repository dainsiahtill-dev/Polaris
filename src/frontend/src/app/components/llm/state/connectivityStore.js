/**
 * Unified Connectivity Store
 * 统一管理所有连通性测试状态，解决组件间状态同步问题
 *
 * 已迁移到 Zustand: 使用 providerStore 而非 ProviderContext
 */
import { useCallback } from "react";
import { useProviderStore } from "@/app/store";
import { resolveProviderConfiguredModel } from "../utils/providerModelResolver";
export function isConnectivityKeyForProvider(key, providerId) {
    if (!key || !providerId)
        return false;
    return key === providerId || key.endsWith(`::${providerId}`);
}
export function extractProviderIdFromConnectivityKey(key) {
    if (!key)
        return "";
    const separator = "::";
    const separatorIndex = key.indexOf(separator);
    if (separatorIndex < 0)
        return key;
    return key.slice(separatorIndex + separator.length);
}
function convertToConnectivityResult(result) {
    return {
        ok: result.ok,
        timestamp: result.timestamp,
        latencyMs: result.latencyMs,
        error: result.error,
        model: result.model,
        sourceRole: result.sourceRole,
        thinking: result.thinking,
    };
}
function mapConnectivityStatusToInterview(status) {
    switch (status) {
        case "success":
            return "ready";
        case "running":
            return "testing";
        case "failed":
            return "failed";
        default:
            return "untested";
    }
}
export function useConnectivityStore() {
    const providerTestStatus = useProviderStore((s) => s.providerTestStatus);
    const connectivityResults = useProviderStore((s) => s.connectivityResults);
    const getLatestProviderConnectivity = useCallback((providerId) => {
        let latestResult = undefined;
        let latestTimestamp = 0;
        Object.entries(connectivityResults).forEach(([key, result]) => {
            if (isConnectivityKeyForProvider(key, providerId)) {
                const timestamp = new Date(result.timestamp).getTime();
                if (timestamp > latestTimestamp) {
                    latestTimestamp = timestamp;
                    latestResult = convertToConnectivityResult(result);
                }
            }
        });
        return latestResult;
    }, [connectivityResults]);
    const getProviderStatus = useCallback((providerId) => {
        return providerTestStatus[providerId] || "unknown";
    }, [providerTestStatus]);
    const getConnectivityResult = useCallback((key) => {
        const result = connectivityResults[key];
        if (!result)
            return undefined;
        return convertToConnectivityResult(result);
    }, [connectivityResults]);
    const getProviderConnectivity = useCallback((providerId) => {
        return getLatestProviderConnectivity(providerId);
    }, [getLatestProviderConnectivity]);
    const buildProviderSummaries = useCallback((providers) => {
        return Object.entries(providers).map(([providerId, provider]) => {
            const status = getProviderStatus(providerId);
            const latestConnectivity = getProviderConnectivity(providerId);
            return {
                id: providerId,
                name: provider.name || providerId,
                model: resolveProviderConfiguredModel(provider),
                providerType: provider.type || "unknown",
                status: mapConnectivityStatusToInterview(status),
                lastTest: latestConnectivity
                    ? {
                        timestamp: latestConnectivity.timestamp,
                        success: latestConnectivity.ok,
                        latencyMs: latestConnectivity.latencyMs,
                        error: latestConnectivity.error,
                    }
                    : undefined,
            };
        });
    }, [getProviderStatus, getProviderConnectivity]);
    const buildConnectivityMap = useCallback(() => {
        const result = new Map();
        Object.entries(connectivityResults).forEach(([key, value]) => {
            result.set(key, convertToConnectivityResult(value));
        });
        return result;
    }, [connectivityResults]);
    const getRoleProviderConnectivity = useCallback((roleId, providerId, model) => {
        const key = `${roleId}::${providerId}`;
        const directResult = getConnectivityResult(key);
        if (directResult && (!model || directResult.model === model)) {
            return directResult;
        }
        return getLatestProviderConnectivity(providerId);
    }, [getConnectivityResult, getLatestProviderConnectivity]);
    const isProviderReady = useCallback((providerId) => {
        const status = getProviderStatus(providerId);
        return status === "success";
    }, [getProviderStatus]);
    return {
        getProviderStatus,
        getConnectivityResult,
        getProviderConnectivity,
        buildProviderSummaries,
        buildConnectivityMap,
        getRoleProviderConnectivity,
        getLatestProviderConnectivity,
        isProviderReady,
    };
}
export function useRoleProviderConnectivity(roleId, providerId, model) {
    const { getProviderStatus, getConnectivityResult, getLatestProviderConnectivity, } = useConnectivityStore();
    const status = getProviderStatus(providerId);
    const directResult = getConnectivityResult(`${roleId}::${providerId}`);
    let result;
    if (directResult && (!model || directResult.model === model)) {
        result = directResult;
    }
    else {
        result = getLatestProviderConnectivity(providerId);
    }
    return {
        status,
        result,
        latency: result?.latencyMs,
        error: result?.error,
        timestamp: result?.timestamp,
    };
}
export function useProviderReadiness(providerId) {
    const { getProviderStatus, getProviderConnectivity } = useConnectivityStore();
    const status = getProviderStatus(providerId);
    const connectivity = getProviderConnectivity(providerId);
    return {
        isReady: status === "success",
        status,
        lastTest: connectivity
            ? {
                timestamp: connectivity.timestamp,
                success: connectivity.ok,
                latencyMs: connectivity.latencyMs,
                error: connectivity.error,
            }
            : undefined,
    };
}
