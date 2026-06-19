import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ContextViewerModal } from '../ContextViewerModal';

// Mock apiFetch so we can inject 403 WORKSPACE_FORBIDDEN responses without
// needing a real backend.
const mockApiFetch = vi.fn();
vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

const SAMPLE_REF = 'abcdef0123456789abcdef01';

afterEach(() => {
  mockApiFetch.mockReset();
});

describe('ContextViewerModal 403 handling', () => {
  it('renders the "other workspace" empty-state when the backend returns 403 WORKSPACE_FORBIDDEN', async () => {
    mockApiFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: {
            code: 'WORKSPACE_FORBIDDEN',
            message: 'Context snapshot belongs to a different workspace',
            details: {},
          },
        }),
        { status: 403, headers: { 'content-type': 'application/json' } },
      ),
    );

    render(
      <ContextViewerModal
        contextSnapshotRef={SAMPLE_REF}
        roleId="director"
        onClose={vi.fn()}
      />,
    );

    // The advisory ACL fires only on a real 403 + WORKSPACE_FORBIDDEN code,
    // so the EmptyState branch must take over from the generic ErrorState.
    const emptyState = await waitFor(() =>
      screen.getByTestId('contextos-viewer-workspace-forbidden'),
    );
    expect(emptyState).toBeInTheDocument();
    expect(emptyState).toHaveTextContent(/工作区/);

    // ErrorState must NOT render in the 403/workspace-forbidden path —
    // that's the whole point of the localised empty-state.
    expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument();
  });

  it('still surfaces generic ErrorState when 403 has a non-workspace code', async () => {
    mockApiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'PERMISSION_DENIED' } }), {
        status: 403,
        headers: { 'content-type': 'application/json' },
      }),
    );

    render(
      <ContextViewerModal
        contextSnapshotRef={SAMPLE_REF}
        roleId="director"
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/HTTP 403/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('contextos-viewer-workspace-forbidden')).not.toBeInTheDocument();
  });

  it('renders the empty-state when no snapshot ref is supplied', () => {
    render(
      <ContextViewerModal
        contextSnapshotRef={null}
        roleId="director"
        onClose={vi.fn()}
      />,
    );
    // Initial empty-state: backend off / no ref captured.
    expect(screen.getByTestId('contextos-viewer-empty')).toBeInTheDocument();
    // No fetch should have been issued.
    expect(mockApiFetch).not.toHaveBeenCalled();
  });
});