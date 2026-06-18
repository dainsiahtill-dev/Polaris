import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DocsInitDialog } from './DocsInitDialog';

vi.mock("@/hooks/useNDJSONStream", () => ({
  useNDJSONStream: (options: {
    onComplete?: (data: Record<string, unknown>) => void;
  } = {}) => ({
    isStreaming: false,
    startStream: vi.fn((path: string) => {
      if (path.includes('/docs/init/preview/jetstream')) {
        options.onComplete?.({
          mode: 'minimal',
          target_root: 'docs/very-long-target-directory-name-without-natural-breaks-for-layout-validation',
          docs_exists: false,
          files: [
            {
              path: 'docs/architecture/very-long-generated-document-file-name-without-natural-breaks.md',
              content: Array.from({ length: 80 }, (_, index) => (
                `# Section ${index + 1}\n${'long-token-'.repeat(18)}`
              )).join('\n'),
              exists: false,
            },
          ],
        });
        return;
      }
      options.onComplete?.({
        reply: 'Ready for draft',
        questions: [],
        fields: {},
        meta: { phase: 'ready_for_draft', answered_slots: [], unresolved_slots: [] },
        tiaochen: [],
      });
    }),
    stopStream: vi.fn(),
  }),
}));

describe('DocsInitDialog', () => {
  it('keeps the discussion layout inside explicit header/body/footer rows', () => {
    const longWorkspace = 'C:\\Temp\\Polaris_Docs_Init_Dialog_With_A_Very_Long_Workspace_Path_That_Should_Wrap';

    render(
      <DocsInitDialog
        open
        onOpenChange={vi.fn()}
        workspace={longWorkspace}
        workspaceStatus={{ status: 'NEEDS_DOCS_INIT' }}
        docsPresent={false}
      />,
    );

    const dialog = screen.getByTestId('docs-init-dialog');
    expect(dialog.className).toContain('grid-rows-[auto_minmax(0,1fr)_auto]');
    expect(dialog.className).toContain('overflow-hidden');
    expect(screen.getByTestId('docs-init-body')).toHaveClass('min-h-0', 'overflow-hidden');
    expect(screen.getByTestId('docs-init-dialogue-step')).toHaveClass('h-full', 'min-h-0', 'overflow-hidden');
    expect(screen.getByTestId('docs-init-input-scroll')).toHaveClass('h-full', 'min-h-0');
    expect(screen.getByTestId('docs-init-dialogue-right')).toHaveClass('h-full', 'min-h-0', 'overflow-hidden');
    expect(screen.getByTestId('docs-init-dialogue-record')).toHaveClass('min-h-0', 'overflow-hidden');
    expect(screen.getByTestId('docs-init-footer')).toHaveClass('flex-wrap');
    expect(screen.getByTestId('docs-init-workspace-label')).toHaveTextContent(
      'Polaris_Docs_Init_Dialog_With_A_Very_Long_Workspace_Path_That_Should_Wrap',
    );
    expect(screen.getByTestId('docs-init-workspace-label')).not.toHaveTextContent('C:\\Temp');
    expect(screen.getByTestId('docs-init-workspace-label')).toHaveAttribute('title', longWorkspace);
  });

  it('keeps generated preview files in a bounded approval scroll region', async () => {
    render(
      <DocsInitDialog
        open
        onOpenChange={vi.fn()}
        workspace="C:\\Temp\\Product"
        workspaceStatus={{ status: 'NEEDS_DOCS_INIT' }}
        docsPresent={false}
      />,
    );

    fireEvent.click(screen.getByTestId('docs-init-build-preview'));

    await waitFor(() => expect(screen.getByTestId('docs-init-approve-step')).toBeInTheDocument());
    expect(screen.getByTestId('docs-init-approve-step')).toHaveClass('h-full', 'min-h-0', 'overflow-hidden');
    expect(screen.getByTestId('docs-init-preview-scroll')).toHaveClass('h-full', 'min-h-0');
    expect(screen.getByText(/very-long-generated-document-file-name/)).toHaveClass('break-all');
    expect(screen.getByTestId('docs-init-footer')).toHaveClass('flex-wrap');
  });
});
