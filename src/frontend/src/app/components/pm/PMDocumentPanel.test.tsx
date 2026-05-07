import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { PMDocumentPanel } from './PMDocumentPanel';

const documentServiceMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock('@/services/pmService', () => ({
  pmDocumentService: documentServiceMock,
}));

vi.mock('sonner', () => ({
  toast: toastMock,
}));

describe('PMDocumentPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render invented documents when PM has no tracked document evidence', async () => {
    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: { documents: [], pagination: { total: 0 } },
    });

    render(
      <PMDocumentPanel
        workspace="C:/Temp/SimpleGame"
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('pm-document-empty')).toHaveTextContent('暂无已跟踪文档');
    expect(screen.queryByText('PRD.md')).not.toBeInTheDocument();
    expect(screen.queryByText('Architecture.md')).not.toBeInTheDocument();
    expect(screen.queryByText('API.md')).not.toBeInTheDocument();
  });

  it('loads and saves real PM documents through the PM document API', async () => {
    const onDocumentSelect = vi.fn();
    const documentPath = 'C:\\Temp\\SimpleGame\\docs\\product\\plan.md';

    documentServiceMock.list
      .mockResolvedValueOnce({
        ok: true,
        data: {
          documents: [
            {
              path: documentPath,
              current_version: '2',
              version_count: 2,
              last_modified: '2026-05-07T07:16:25Z',
              created_at: '2026-05-07T07:00:00Z',
            },
          ],
          pagination: { total: 1 },
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          documents: [
            {
              path: documentPath,
              current_version: '3',
              version_count: 3,
              last_modified: '2026-05-07T07:20:00Z',
              created_at: '2026-05-07T07:00:00Z',
            },
          ],
          pagination: { total: 1 },
        },
      });
    documentServiceMock.get.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        current_version: '2',
        version_count: 2,
        last_modified: '2026-05-07T07:16:25Z',
        created_at: '2026-05-07T07:00:00Z',
        content: '# Real Plan',
      },
    });
    documentServiceMock.save.mockResolvedValueOnce({
      ok: true,
      data: { success: true, path: documentPath, version: '3', checksum: 'abc123' },
    });

    render(
      <PMDocumentPanel
        workspace="C:/Temp/SimpleGame"
        selectedPath={null}
        onDocumentSelect={onDocumentSelect}
      />,
    );

    const documentEntry = await screen.findByText('plan.md');
    fireEvent.click(documentEntry);

    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath));
    expect(onDocumentSelect).toHaveBeenCalledWith(documentPath);
    expect(await screen.findByText('Real Plan')).toBeInTheDocument();
    expect(screen.getByTestId('pm-document-provenance')).toHaveTextContent(
      'PM docs API · v2 · modified 2026-05-07T07:16:25Z',
    );

    fireEvent.click(screen.getByText('编辑'));
    fireEvent.change(screen.getByDisplayValue('# Real Plan'), { target: { value: '# Updated Plan' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(documentServiceMock.save).toHaveBeenCalledWith(
        documentPath,
        '# Updated Plan',
        'Updated from PM document workspace',
      );
    });
    expect(toastMock.success).toHaveBeenCalledWith('文件已保存');
    await waitFor(() => {
      expect(screen.getByTestId('pm-document-provenance')).toHaveTextContent('PM docs API · v3');
    });
  });
});
