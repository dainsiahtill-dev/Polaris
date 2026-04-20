/** useInstantFeedback - 即时反馈 Hook
 *
 * 提供统一的即时反馈能力：
 * - 按钮点击即时响应
 * - Toast 通知
 * - 状态追踪
 * - 错误处理
 */
import { useState, useCallback } from 'react';
import { toast } from 'sonner';

type AsyncFunction<T = void> = () => Promise<T>;

interface UseInstantFeedbackOptions {
  /** 成功消息 */
  successMessage?: string;
  /** 错误消息前缀 */
  errorMessage?: string;
  /** 加载消息 */
  loadingMessage?: string;
  /** 成功后自动显示消息 */
  showSuccessToast?: boolean;
  /** 错误后自动显示消息 */
  showErrorToast?: boolean;
}

interface FeedbackState {
  isLoading: boolean;
  error: string | null;
  lastSuccess: boolean;
}

export function useInstantFeedback<T = void>(options: UseInstantFeedbackOptions = {}) {
  const {
    successMessage = '操作成功',
    errorMessage = '操作失败',
    loadingMessage = '处理中...',
    showSuccessToast = true,
    showErrorToast = true,
  } = options;

  const [state, setState] = useState<FeedbackState>({
    isLoading: false,
    error: null,
    lastSuccess: false,
  });

  const execute = useCallback(async (fn: AsyncFunction<T>): Promise<T | undefined> => {
    // 立即设置加载状态
    setState(prev => ({ ...prev, isLoading: true, error: null }));
    
    // 显示加载 toast（如果有）
    if (loadingMessage) {
      toast.loading(loadingMessage, { id: 'action-toast' });
    }

    try {
      const result = await fn();
      
      // 成功
      setState({ isLoading: false, error: null, lastSuccess: true });
      
      if (showSuccessToast) {
        toast.success(successMessage, { id: 'action-toast' });
      }
      
      return result;
      
    } catch (error) {
      // 错误
      const errorMsg = error instanceof Error ? error.message : errorMessage;
      setState({ isLoading: false, error: errorMsg, lastSuccess: false });
      
      if (showErrorToast) {
        toast.error(errorMsg, { id: 'action-toast' });
      }
      
      return undefined;
    }
  }, [successMessage, errorMessage, loadingMessage, showSuccessToast, showErrorToast]);

  const reset = useCallback(() => {
    setState({ isLoading: false, error: null, lastSuccess: false });
  }, []);

  return {
    ...state,
    execute,
    reset,
  };
}

/** 操作反馈 Hook - 更简单的版本
 * 
 * 用法:
 * const { isLoading, execute } = useActionFeedback();
 * 
 * <button onClick={() => execute(async () => { await apiCall(); })}>
 */
export function useActionFeedback() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async <T,>(
    fn: () => Promise<T>,
    options?: {
      loadingMessage?: string;
      successMessage?: string;
      errorMessage?: string;
    }
  ): Promise<T | undefined> => {
    setIsLoading(true);
    setError(null);

    const loadingId = 'action-' + Date.now();
    
    if (options?.loadingMessage) {
      toast.loading(options.loadingMessage, { id: loadingId });
    }

    try {
      const result = await fn();
      
      if (options?.successMessage) {
        toast.success(options.successMessage, { id: loadingId });
      } else {
        toast.dismiss(loadingId);
      }
      
      setIsLoading(false);
      return result;
      
    } catch (err) {
      const errorMsg = options?.errorMessage || (err instanceof Error ? err.message : '操作失败');
      setError(errorMsg);
      toast.error(errorMsg, { id: loadingId });
      setIsLoading(false);
      return undefined;
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return {
    isLoading,
    error,
    execute,
    clearError,
  };
}

/** 带确认的操作 Hook */
export function useConfirmAction() {
  const [isConfirming, setIsConfirming] = useState<string | null>(null);

  const confirm = useCallback(async <T,>(
    actionId: string,
    fn: () => Promise<T>,
    options?: {
      confirmMessage?: string;
      loadingMessage?: string;
      successMessage?: string;
      errorMessage?: string;
    }
  ): Promise<T | undefined> => {
    // 防止重复确认
    if (isConfirming) return undefined;
    
    setIsConfirming(actionId);

    const loadingId = 'confirm-' + actionId;
    
    try {
      if (options?.confirmMessage) {
        // 显示确认 toast
        toast.message(options.confirmMessage, { 
          id: loadingId,
          duration: 5,
        });
      }

      const result = await fn();

      if (options?.successMessage) {
        toast.success(options.successMessage, { id: loadingId });
      } else {
        toast.dismiss(loadingId);
      }

      return result;
      
    } catch (err) {
      const errorMsg = options?.errorMessage || (err instanceof Error ? err.message : '操作失败');
      toast.error(errorMsg, { id: loadingId });
      return undefined;
      
    } finally {
      setIsConfirming(null);
    }
  }, [isConfirming]);

  return {
    isConfirming,
    confirm,
  };
}
