import { useState, useCallback, useEffect, useRef } from 'react';
import { fileService, agentsService } from '@/services/api';
import { toast } from 'sonner';
import type { AgentsReviewInfo } from '@/app/types/appContracts';
import { normalizeAgentsFeedback } from '@/app/utils/appRuntime';

export interface UseAgentsReviewOptions {
  agentsReview: AgentsReviewInfo | null;
  isOpen: boolean;
  runtimeIssue: unknown;
}

export function useAgentsReview(options: UseAgentsReviewOptions) {
  const { agentsReview, isOpen, runtimeIssue } = options;

  const [draftContent, setDraftContent] = useState('');
  const [draftMtime, setDraftMtime] = useState('');
  const [feedback, setFeedback] = useState('');
  const [feedbackSavedAt, setFeedbackSavedAt] = useState('');
  const [feedbackDirty, setFeedbackDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);

  const holdRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!agentsReview || !agentsReview.needs_review) return;

    setLoading(true);

    if (agentsReview.draft_path) {
      const result = await fileService.read(agentsReview.draft_path, 2000);
      if (result.ok && result.data) {
        setDraftContent(result.data.content || '');
        setDraftMtime(result.data.mtime || '');
      } else {
        setDraftContent('(draft missing)');
        setDraftMtime('');
      }
    } else {
      setDraftContent('(draft missing)');
      setDraftMtime('');
    }

    if (agentsReview.feedback_path && !feedbackDirty) {
      const result = await fileService.read(agentsReview.feedback_path, 2000);
      if (result.ok && result.data) {
        setFeedback(normalizeAgentsFeedback(result.data.content || ''));
        setFeedbackSavedAt(result.data.mtime || '');
      }
    }

    setLoading(false);
  }, [agentsReview, feedbackDirty]);

  const saveFeedback = useCallback(async () => {
    if (!agentsReview?.needs_review) return false;

    const result = await agentsService.saveFeedback(feedback);

    if (result.ok && result.data) {
      setFeedbackDirty(false);

      if (result.data.cleared) {
        setFeedbackSavedAt('');
      } else if (result.data.mtime) {
        setFeedbackSavedAt(result.data.mtime);
      }

      if (agentsReview.draft_mtime) {
        holdRef.current = agentsReview.draft_mtime;
      } else if (draftMtime) {
        holdRef.current = draftMtime;
      }

      toast.info('反馈已提交，正在等待尚书令重新生成草稿...');
      return true;
    } else {
      toast.error(result.error || 'Failed to save feedback');
      return false;
    }
  }, [agentsReview, feedback, draftMtime]);

  const applyDraft = useCallback(async () => {
    if (!agentsReview?.needs_review || applying) return false;

    setApplying(true);

    const result = await agentsService.applyDraft(agentsReview.draft_path!);

    setApplying(false);

    if (result.ok) {
      holdRef.current = null;
      setDraftContent('');
      setDraftMtime('');
      setFeedback('');
      setFeedbackDirty(false);
      toast.success('AGENTS.md 已更新');
      return true;
    } else {
      toast.error(result.error || 'Failed to apply AGENTS draft');
      return false;
    }
  }, [agentsReview, applying]);

  const updateFeedback = useCallback((value: string) => {
    setFeedback(value);
    setFeedbackDirty(true);
  }, []);

  useEffect(() => {
    if (isOpen) {
      load();
    }
  }, [isOpen, agentsReview?.draft_path, agentsReview?.feedback_path, agentsReview?.draft_mtime, load]);

  const draftFailed = agentsReview?.draft_failed ||
    draftContent.toLowerCase().includes('generation failed') ||
    draftContent.toLowerCase().includes('failed to write last message file');

  return {
    draftContent,
    draftMtime,
    draftFailed,
    feedback,
    feedbackSavedAt,
    feedbackDirty,
    loading,
    applying,
    load,
    saveFeedback,
    applyDraft,
    updateFeedback,
    setDraftContent,
  };
}
