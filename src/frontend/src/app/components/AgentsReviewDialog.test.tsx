import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentsReviewDialog } from './AgentsReviewDialog';

const baseProps = {
  open: true,
  onOpenChange: vi.fn(),
  agentsDraftFailed: false,
  agentsReview: {
    needs_review: true,
    has_agents: false,
    draft_path: 'runtime/contracts/agents.generated.md',
  },
  onOpenLogs: vi.fn(),
  onOpenDraft: vi.fn(),
  workspace: 'C:\\Temp\\workspace',
  agentsDraftMtime: '2026-05-30T10:00:00Z',
  agentsFeedbackSavedAt: '',
  agentsLoading: false,
  agentsDraftContent: Array.from({ length: 80 }, (_, index) => `Rule ${index + 1}: keep UI readable.`).join('\n'),
  agentsFeedback: '',
  onAgentsFeedbackChange: vi.fn(),
  onRetryGenerate: vi.fn(),
  onSubmitFeedback: vi.fn(),
  onApplyDraft: vi.fn(),
  agentsApplying: false,
};

describe('AgentsReviewDialog', () => {
  it('keeps long drafts inside a bounded dialog with visible footer actions', () => {
    const longWorkspace = 'C:\\Temp\\workspace-with-a-very-long-folder-name-that-should-wrap-instead-of-overflowing';

    render(
      <AgentsReviewDialog
        {...baseProps}
        agentsReview={{
          ...baseProps.agentsReview,
          draft_path:
            'runtime/contracts/very-long-agents-review-path-without-natural-breaks/AGENTS.generated.md',
        }}
        workspace={longWorkspace}
      />,
    );

    const dialog = screen.getByTestId('agents-review-dialog');
    expect(dialog.className).toContain('max-h-[92vh]');
    expect(dialog.className).toContain('grid-rows-[auto_auto_minmax(0,1fr)_auto]');
    expect(dialog.className).toContain('overflow-hidden');
    expect(screen.getByTestId('agents-review-scroll-region')).toHaveClass('min-h-0', 'overflow-y-auto');
    expect(screen.getByTestId('agents-review-footer')).toHaveClass('flex-wrap');
    expect(screen.getByText(/草案路径:/)).toHaveClass('break-all');
    expect(screen.getByText(/目标位置:/)).toHaveClass('truncate');
    expect(screen.getByTestId('agents-review-target-label')).toHaveTextContent(
      'workspace-with-a-very-long-folder-name-that-should-wrap-instead-of-overflowing\\AGENTS.md',
    );
    expect(screen.getByText(/目标位置:/)).toHaveAttribute(
      'title',
      `${longWorkspace}\\AGENTS.md`,
    );
    expect(screen.getByText('AGENTS.md 草案待审')).toBeInTheDocument();
    expect(screen.getByText('回写到 AGENTS.md')).toBeInTheDocument();
    expect(screen.getByText('提交反馈（暂不回写）')).toBeInTheDocument();
  });
});
