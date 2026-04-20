import { useState, useCallback, useRef } from 'react';
import { getLLMConfig, saveLLMConfig, getLLMStatus } from '@/services';
import type { LLMConfigResponse, LLMStatusResponse } from '@/services';

export interface UseLlmConfigOptions {
  onStatusChange?: (status: LLMStatusResponse | null) => void;
}

export function useLlmConfig(options: UseLlmConfigOptions = {}) {
  const { onStatusChange } = options;

  const [config, setConfig] = useState<LLMConfigResponse | null>(null);
  const [status, setStatus] = useState<LLMStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const configRef = useRef<LLMConfigResponse | null>(null);
  const lastSavedRef = useRef<LLMConfigResponse | null>(null);
  const saveQueueRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const pendingSaveRef = useRef<LLMConfigResponse | null>(null);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);

    const result = await getLLMConfig();

    if (result.ok && result.data) {
      setConfig(result.data);
      configRef.current = result.data;
      lastSavedRef.current = result.data;
      setLoading(false);
      return result.data;
    } else {
      setError(result.error || '读取 LLM 配置失败');
      setLoading(false);
      return null;
    }
  }, []);

  const loadStatus = useCallback(async () => {
    const result = await getLLMStatus();

    if (result.ok && result.data) {
      setStatus(result.data);
      onStatusChange?.(result.data);
      return result.data;
    } else {
      setStatus(null);
      onStatusChange?.(null);
      return null;
    }
  }, [onStatusChange]);

  const queueSave = useCallback(async (newConfig: LLMConfigResponse): Promise<boolean> => {
    pendingSaveRef.current = newConfig;

    const run = async (): Promise<boolean> => {
      if (!pendingSaveRef.current) return true;
      setSaving(true);
      setError(null);

      while (pendingSaveRef.current) {
        const toSave = pendingSaveRef.current;
        pendingSaveRef.current = null;

        const result = await saveLLMConfig(toSave);

        if (!result.ok || !result.data) {
          setError(result.error || '保存 LLM 配置失败');
          pendingSaveRef.current = null;
          setSaving(false);
          return false;
        }

        setConfig(result.data);
        configRef.current = result.data;
        lastSavedRef.current = result.data;
        await loadStatus();
      }

      setSaving(false);
      return true;
    };

    saveQueueRef.current = saveQueueRef.current.then(run, run);
    return saveQueueRef.current;
  }, [loadStatus]);

  const updateRole = useCallback((role: string, updates: Partial<LLMConfigResponse['roles'][string]>) => {
    setConfig(prev => {
      if (!prev) return prev;
      const next = {
        ...prev,
        roles: {
          ...prev.roles,
          [role]: {
            ...prev.roles[role],
            ...updates,
          },
        },
      };
      configRef.current = next;
      return next;
    });
  }, []);

  const updateProvider = useCallback((providerId: string, updates: Partial<LLMConfigResponse['providers'][string]>) => {
    setConfig(prev => {
      if (!prev) return prev;
      const next = {
        ...prev,
        providers: {
          ...prev.providers,
          [providerId]: {
            ...prev.providers?.[providerId],
            ...updates,
          },
        },
      };
      configRef.current = next;
      return next;
    });
  }, []);

  const addProvider = useCallback((providerId: string, provider: LLMConfigResponse['providers'][string]) => {
    setConfig(prev => {
      if (!prev) return prev;
      const next = {
        ...prev,
        providers: {
          ...prev.providers,
          [providerId]: provider,
        },
      };
      configRef.current = next;
      return next;
    });
  }, []);

  const removeProvider = useCallback((providerId: string) => {
    setConfig(prev => {
      if (!prev) return prev;
      const nextProviders = { ...prev.providers };
      delete nextProviders[providerId];

      const nextRoles = { ...prev.roles };
      Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
        if (roleCfg?.provider_id === providerId) {
          nextRoles[roleId] = { ...roleCfg, provider_id: '', model: '' };
        }
      });

      const next = {
        ...prev,
        providers: nextProviders,
        roles: nextRoles,
      };
      configRef.current = next;
      return next;
    });
  }, []);

  const save = useCallback(() => {
    if (configRef.current) {
      return queueSave(configRef.current);
    }
    return Promise.resolve(true);
  }, [queueSave]);

  return {
    config,
    status,
    loading,
    saving,
    error,
    loadConfig,
    loadStatus,
    updateRole,
    updateProvider,
    addProvider,
    removeProvider,
    save,
    setConfig,
  };
}
