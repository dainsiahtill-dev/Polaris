/**
 * useProviderForm Hook
 * 
 * 作为 Context 状态与表单组件之间的连接器 (Connector Hook)
 * 提供统一的方式来处理 Provider 表单的编辑、验证和保存
 */

import { useCallback, useMemo } from 'react';
import type { ProviderConfig } from '../types';
import {
  useProviderContext,
  useEditingProviderId,
  useEditFormState,
  useHasPendingChanges,
  useIsSavingProvider,
  useProviderError,
} from './ProviderContext';

export interface UseProviderFormOptions {
  providerId: string;
  initialConfig: ProviderConfig;
  onSave?: (providerId: string, config: ProviderConfig) => Promise<void> | void;
  onValidate?: (config: ProviderConfig) => { valid: boolean; errors: string[]; warnings: string[] };
}

export interface UseProviderFormReturn {
  // 当前表单值 (来自 Context 的 editFormState)
  formState: ProviderConfig;
  // 是否有未保存的更改
  hasPendingChanges: boolean;
  // 是否正在保存
  isSaving: boolean;
  // 是否正在编辑当前 provider
  isEditing: boolean;
  // 错误信息
  error: string | undefined;
  // 验证错误
  validationErrors: string[];
  validationWarnings: string[];
  
  // Actions
  // 开始编辑
  startEdit: () => void;
  // 更新表单字段
  updateField: <K extends keyof ProviderConfig>(
    field: K,
    value: ProviderConfig[K]
  ) => void;
  // 批量更新表单
  updateFields: (updates: Partial<ProviderConfig>) => void;
  // 保存表单
  saveForm: () => Promise<void>;
  // 取消编辑
  cancelEdit: () => void;
  // 清除错误
  clearError: () => void;
}

/**
 * useProviderForm Hook
 * 
 * 使用示例:
 * ```tsx
 * function ProviderCard({ providerId, providerConfig }) {
 *   const form = useProviderForm({
 *     providerId,
 *     initialConfig: providerConfig,
 *     onSave: async (id, config) => {
 *       await saveToBackend(id, config);
 *     }
 *   });
 * 
 *   return (
 *     <div>
 *       {form.isEditing ? (
 *         <ProviderForm
 *           config={form.formState}
 *           onUpdate={form.updateFields}
 *           hasChanges={form.hasPendingChanges}
 *         />
 *       ) : (
 *         <ProviderDisplay config={providerConfig} />
 *       )}
 *       
 *       {form.hasPendingChanges && (
 *         <div className="unsaved-indicator">未保存的更改</div>
 *       )}
 *       
 *       <button onClick={form.isEditing ? form.saveForm : form.startEdit}>
 *         {form.isEditing ? '保存' : '编辑'}
 *       </button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useProviderForm(options: UseProviderFormOptions): UseProviderFormReturn {
  const { providerId, initialConfig, onSave, onValidate } = options;
  
  const {
    startEdit,
    updateEditForm,
    saveEditStart,
    saveEditSuccess,
    saveEditFailure,
    cancelEdit,
    clearProviderError,
  } = useProviderContext();
  
  // 从 Context 获取状态
  const editingProviderId = useEditingProviderId();
  const editFormState = useEditFormState(providerId);
  const hasPendingChanges = useHasPendingChanges(providerId);
  const isSaving = useIsSavingProvider(providerId);
  const error = useProviderError(providerId);
  
  // 是否正在编辑当前 provider
  const isEditing = editingProviderId === providerId;
  
  // 当前表单值 - 如果在编辑中使用 editFormState，否则使用 initialConfig
  const formState = useMemo(() => {
    if (isEditing && editFormState) {
      return editFormState;
    }
    return initialConfig;
  }, [isEditing, editFormState, initialConfig]);
  
  // 验证状态
  const validation = useMemo(() => {
    if (!onValidate) {
      return { valid: true, errors: [] as string[], warnings: [] as string[] };
    }
    return onValidate(formState);
  }, [formState, onValidate]);
  
  // 开始编辑
  const handleStartEdit = useCallback(() => {
    startEdit(providerId, initialConfig);
  }, [providerId, initialConfig, startEdit]);
  
  // 更新单个字段
  const handleUpdateField = useCallback(<K extends keyof ProviderConfig>(
    field: K,
    value: ProviderConfig[K]
  ) => {
    updateEditForm(providerId, { [field]: value } as Partial<ProviderConfig>);
  }, [providerId, updateEditForm]);
  
  // 批量更新字段
  const handleUpdateFields = useCallback((updates: Partial<ProviderConfig>) => {
    updateEditForm(providerId, updates);
  }, [providerId, updateEditForm]);
  
  // 保存表单
  const handleSaveForm = useCallback(async () => {
    // 验证
    if (onValidate) {
      const result = onValidate(formState);
      if (!result.valid) {
        saveEditFailure(providerId, result.errors.join(', '));
        return;
      }
    }
    
    // 开始保存
    saveEditStart(providerId);
    
    try {
      if (onSave) {
        await onSave(providerId, formState);
      }
      saveEditSuccess(providerId);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '保存失败';
      saveEditFailure(providerId, errorMessage);
      throw err;
    }
  }, [providerId, formState, onSave, onValidate, saveEditStart, saveEditSuccess, saveEditFailure]);
  
  // 取消编辑
  const handleCancelEdit = useCallback(() => {
    cancelEdit(providerId);
  }, [providerId, cancelEdit]);
  
  // 清除错误
  const handleClearError = useCallback(() => {
    clearProviderError(providerId);
  }, [providerId, clearProviderError]);
  
  return {
    // State
    formState,
    hasPendingChanges,
    isSaving,
    isEditing,
    error,
    validationErrors: validation.errors,
    validationWarnings: validation.warnings,
    
    // Actions
    startEdit: handleStartEdit,
    updateField: handleUpdateField,
    updateFields: handleUpdateFields,
    saveForm: handleSaveForm,
    cancelEdit: handleCancelEdit,
    clearError: handleClearError,
  };
}

/**
 * useProviderFormList Hook
 * 
 * 用于管理多个 Provider 表单的列表场景
 */
export function useProviderFormList() {
  const { state } = useProviderContext();
  
  return {
    // 全局未保存更改数量
    pendingChangesCount: state.pendingChanges.size,
    // 是否有任何未保存的更改
    hasAnyPendingChanges: state.pendingChanges.size > 0,
    // 正在保存的 provider
    savingProviderId: state.savingProvider,
    // 当前正在编辑的 provider
    editingProviderId: state.editingProviderId,
    // 所有有未保存更改的 provider IDs
    pendingProviderIds: Array.from(state.pendingChanges),
  };
}

export default useProviderForm;
