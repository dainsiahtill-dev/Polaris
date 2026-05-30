import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { PMDocumentPanel } from './PMDocumentPanel';

const workspace = 'C:/Temp/SimpleGame';

const documentServiceMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  delete: vi.fn(),
  search: vi.fn(),
  versions: vi.fn(),
  compare: vi.fn(),
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
    documentServiceMock.versions.mockResolvedValue({
      ok: true,
      data: { path: '', versions: [] },
    });
    documentServiceMock.delete.mockResolvedValue({
      ok: true,
      data: { success: true, path: '', deleted: true },
    });
  });

  it('does not render invented documents when PM has no tracked document evidence', async () => {
    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: { documents: [], pagination: { total: 0 } },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('pm-document-empty')).toHaveTextContent('暂无已跟踪文档');
    expect(screen.queryByText('PRD.md')).not.toBeInTheDocument();
    expect(screen.queryByText('Architecture.md')).not.toBeInTheDocument();
    expect(screen.queryByText('API.md')).not.toBeInTheDocument();
  });

  it('renders PM idle document projections without error banners', async () => {
    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: {
        documents: [],
        pagination: { total: 0, limit: 100, offset: 0 },
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });
    documentServiceMock.search.mockResolvedValueOnce({
      ok: true,
      data: {
        query: 'plan',
        results: [],
        count: 0,
        initialized: false,
        reason: 'PM_NOT_INITIALIZED',
      },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('pm-document-empty')).toHaveTextContent('暂无已跟踪文档');
    expect(screen.queryByTestId('pm-document-error')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('搜索文档...'), { target: { value: 'plan' } });

    await waitFor(() => expect(documentServiceMock.search).toHaveBeenCalledWith('plan', 20, workspace));
    expect(await screen.findByTestId('pm-document-search-empty')).toHaveTextContent('后端未返回匹配文档');
    expect(screen.queryByTestId('pm-document-search-error')).not.toBeInTheDocument();
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
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={onDocumentSelect}
      />,
    );

    const documentEntry = await screen.findByText('plan.md');
    fireEvent.click(documentEntry);

    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath, null, workspace));
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
        workspace,
      );
    });
    expect(toastMock.success).toHaveBeenCalledWith('文件已保存');
    await waitFor(() => {
      expect(screen.getByTestId('pm-document-provenance')).toHaveTextContent('PM docs API · v3');
    });
  });

  it('uses backend document search results to open matching PM documents', async () => {
    const onDocumentSelect = vi.fn();
    const documentPath = 'C:\\Temp\\SimpleGame\\docs\\quality\\gate.md';

    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: {
        documents: [
          {
            path: documentPath,
            current_version: '1',
            version_count: 1,
            last_modified: '2026-05-08T07:16:25Z',
            created_at: '2026-05-08T07:00:00Z',
          },
        ],
        pagination: { total: 1 },
      },
    });
    documentServiceMock.search.mockResolvedValueOnce({
      ok: true,
      data: {
        query: 'quality',
        results: [
          {
            path: documentPath,
            snippet: 'quality gate passed with backend evidence',
            line: 12,
            score: 0.91,
          },
        ],
        count: 1,
      },
    });
    documentServiceMock.get.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        current_version: '1',
        version_count: 1,
        last_modified: '2026-05-08T07:16:25Z',
        created_at: '2026-05-08T07:00:00Z',
        content: '# Quality Gate Evidence',
      },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={onDocumentSelect}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('搜索文档...'), { target: { value: 'quality' } });

    await waitFor(() => expect(documentServiceMock.search).toHaveBeenCalledWith('quality', 20, workspace));
    expect(await screen.findByTestId('pm-document-search-results')).toHaveTextContent(
      'quality gate passed with backend evidence',
    );

    fireEvent.click(screen.getByTestId('pm-document-search-result'));

    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath, null, workspace));
    expect(onDocumentSelect).toHaveBeenCalledWith(documentPath);
    expect(await screen.findByText('Quality Gate Evidence')).toBeInTheDocument();
  });

  it('loads document versions and compares the latest two versions through the PM API', async () => {
    const documentPath = 'C:\\Temp\\SimpleGame\\docs\\product\\plan.md';

    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: {
        documents: [
          {
            path: documentPath,
            current_version: '2',
            version_count: 2,
            last_modified: '2026-05-09T07:16:25Z',
            created_at: '2026-05-09T07:00:00Z',
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
        last_modified: '2026-05-09T07:16:25Z',
        created_at: '2026-05-09T07:00:00Z',
        content: '# Versioned Plan',
      },
    });
    documentServiceMock.versions.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        versions: [
          {
            version: '1',
            created_at: '2026-05-09T07:00:00Z',
            created_by: 'pm',
            change_summary: 'Initial plan',
            checksum: 'aaa',
          },
          {
            version: '2',
            created_at: '2026-05-09T07:16:25Z',
            created_by: 'pm',
            change_summary: 'Added QA criteria',
            checksum: 'bbb',
          },
        ],
      },
    });
    documentServiceMock.compare.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        old_version: '1',
        new_version: '2',
        diff_text: '+ Added QA criteria',
        changed_sections: ['QA'],
        added_requirements: ['QA criteria'],
        removed_requirements: [],
        impact_score: 0.3,
      },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByText('plan.md'));

    await waitFor(() => expect(documentServiceMock.versions).toHaveBeenCalledWith(documentPath, workspace));
    expect(await screen.findByTestId('pm-document-version-list')).toHaveTextContent('Added QA criteria');

    fireEvent.click(screen.getByText('比较最新'));

    await waitFor(() => expect(documentServiceMock.compare).toHaveBeenCalledWith(documentPath, '1', '2', workspace));
    expect(await screen.findByTestId('pm-document-diff')).toHaveTextContent('+ Added QA criteria');
  });

  it('opens historical PM document versions as read-only backend content', async () => {
    const documentPath = 'C:\\Temp\\SimpleGame\\docs\\product\\plan.md';

    documentServiceMock.list.mockResolvedValueOnce({
      ok: true,
      data: {
        documents: [
          {
            path: documentPath,
            current_version: '2',
            version_count: 2,
            last_modified: '2026-05-11T07:16:25Z',
            created_at: '2026-05-11T07:00:00Z',
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
        last_modified: '2026-05-11T07:16:25Z',
        created_at: '2026-05-11T07:00:00Z',
        content: '# Current Plan',
      },
    });
    documentServiceMock.versions.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        versions: [
          {
            version: '1',
            created_at: '2026-05-11T07:00:00Z',
            created_by: 'pm',
            change_summary: 'Initial historical plan',
            checksum: 'aaa',
          },
          {
            version: '2',
            created_at: '2026-05-11T07:16:25Z',
            created_by: 'pm',
            change_summary: 'Current plan',
            checksum: 'bbb',
          },
        ],
      },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByText('plan.md'));
    expect(await screen.findByText('Current Plan')).toBeInTheDocument();

    documentServiceMock.get.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        current_version: '2',
        version_count: 2,
        last_modified: '2026-05-11T07:16:25Z',
        created_at: '2026-05-11T07:00:00Z',
        content: '# Historical Plan',
      },
    });

    fireEvent.click((await screen.findAllByTestId('pm-document-version-open'))[1]);

    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath, '1', workspace));
    expect(await screen.findByText('Historical Plan')).toBeInTheDocument();
    expect(screen.getByTestId('pm-document-version-read-evidence')).toHaveTextContent('version=1');
    expect(screen.getByRole('button', { name: /编辑/ })).toBeDisabled();

    documentServiceMock.get.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        current_version: '2',
        version_count: 2,
        last_modified: '2026-05-11T07:16:25Z',
        created_at: '2026-05-11T07:00:00Z',
        content: '# Current Plan',
      },
    });

    fireEvent.click(screen.getByTestId('pm-document-current-version'));

    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath, null, workspace));
    expect(await screen.findByText('Current Plan')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /编辑/ })).not.toBeDisabled();
  });

  it('deletes PM document records through the guarded backend delete route', async () => {
    const documentPath = 'C:\\Temp\\SimpleGame\\docs\\product\\obsolete.md';

    documentServiceMock.list
      .mockResolvedValueOnce({
        ok: true,
        data: {
          documents: [
            {
              path: documentPath,
              current_version: '4',
              version_count: 4,
              last_modified: '2026-05-10T07:16:25Z',
              created_at: '2026-05-10T07:00:00Z',
            },
          ],
          pagination: { total: 1 },
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          documents: [],
          pagination: { total: 0 },
        },
      });
    documentServiceMock.get.mockResolvedValueOnce({
      ok: true,
      data: {
        path: documentPath,
        current_version: '4',
        version_count: 4,
        last_modified: '2026-05-10T07:16:25Z',
        created_at: '2026-05-10T07:00:00Z',
        content: '# Obsolete Plan',
      },
    });
    documentServiceMock.delete.mockResolvedValueOnce({
      ok: true,
      data: {
        success: true,
        path: documentPath,
        deleted: true,
      },
    });

    render(
      <PMDocumentPanel
        workspace={workspace}
        selectedPath={null}
        onDocumentSelect={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByText('obsolete.md'));
    await waitFor(() => expect(documentServiceMock.get).toHaveBeenCalledWith(documentPath, null, workspace));

    fireEvent.click(screen.getByTestId('pm-document-delete-toggle'));

    expect(screen.getByTestId('pm-document-delete-panel')).not.toHaveTextContent('/v2/pm/documents/docs/product/obsolete.md');
    expect(screen.getByTestId('pm-document-delete-endpoint')).toHaveTextContent('DELETE API');
    expect(screen.getByTestId('pm-document-delete-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/pm/documents/docs/product/obsolete.md',
    );
    expect(screen.getByTestId('pm-document-delete-evidence')).toHaveTextContent('delete_file=false');
    expect(screen.getByTestId('pm-document-delete-delete-file')).not.toBeChecked();

    fireEvent.click(screen.getByTestId('pm-document-delete-submit'));

    await waitFor(() => expect(documentServiceMock.delete).toHaveBeenCalledWith(documentPath, false, workspace));
    expect(toastMock.success).toHaveBeenCalledWith('PM 文档记录已删除');
    await waitFor(() => expect(documentServiceMock.list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('选择文档以查看')).toBeInTheDocument();
  });
});
