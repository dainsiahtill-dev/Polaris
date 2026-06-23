import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceFilesPage } from './WorkspaceFilesPage';
import type { WorkspaceFileTreeResponse } from '@/services';

const serviceMocks = vi.hoisted(() => ({
  listWorkspaceFileTree: vi.fn(),
  readScopedFile: vi.fn(),
}));

vi.mock('@/services', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services')>()),
  listWorkspaceFileTree: serviceMocks.listWorkspaceFileTree,
  readScopedFile: serviceMocks.readScopedFile,
}));

vi.mock('@monaco-editor/react', () => ({
  default: ({ value, loading }: { value?: string; loading?: ReactNode }) => (
    <div>
      <div data-testid="workspace-monaco-loading">{loading}</div>
      <textarea data-testid="workspace-monaco-preview" readOnly value={value || ''} />
    </div>
  ),
}));

function makeTree({
  fileName = 'index.ts',
  filePath = 'src/index.ts',
  scope = 'workspace',
}: {
  fileName?: string;
  filePath?: string;
  scope?: string;
} = {}): WorkspaceFileTreeResponse {
  return {
    workspace: '/repo',
    scope,
    root: '',
    generated_at: '2026-06-23T00:00:00+00:00',
    max_depth: 12,
    max_entries: 6000,
    truncated: false,
    excluded: ['node_modules'],
    stats: {
      files: 3,
      directories: 2,
      omitted: 1,
      hidden: 0,
      binary: 1,
      total_size: 2048,
    },
    tree: {
      id: `${scope}:.`,
      name: 'repo',
      path: '',
      type: 'directory',
      depth: 0,
      icon: 'folder',
      children: [
        {
          id: `${scope}:src`,
          name: 'src',
          path: 'src',
          type: 'directory',
          depth: 1,
          icon: 'folder',
          children: [
            {
              id: `${scope}:${filePath}`,
              name: fileName,
              path: filePath,
              type: 'file',
              depth: 2,
              extension: '.ts',
              language: 'typescript',
              mime: 'text/typescript',
              icon: 'typescript',
              size: 21,
              mtime: '2026-06-23T00:00:00+00:00',
              is_binary: false,
            },
            {
              id: 'workspace:src/logo.png',
              name: 'logo.png',
              path: 'src/logo.png',
              type: 'file',
              depth: 2,
              extension: '.png',
              mime: 'image/png',
              icon: 'image',
              size: 1024,
              mtime: '2026-06-23T00:00:00+00:00',
              is_binary: true,
            },
          ],
        },
      ],
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe('WorkspaceFilesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    serviceMocks.listWorkspaceFileTree.mockResolvedValue({ ok: true, data: makeTree() });
    serviceMocks.readScopedFile.mockResolvedValue({
      ok: true,
      data: { content: 'export const ok = true;\n', mtime: '2026-06-23T00:00:00+00:00' },
    });
  });

  it('loads the workspace tree and previews the first text file in Monaco', async () => {
    render(<WorkspaceFilesPage workspace="/repo" onBackToMain={vi.fn()} />);

    expect(await screen.findByText('index.ts')).toBeInTheDocument();
    await waitFor(() => {
      expect(serviceMocks.readScopedFile).toHaveBeenCalledWith('src/index.ts', 'workspace');
    });
    expect(screen.getByTestId('workspace-monaco-preview')).toHaveValue('export const ok = true;\n');
    expect(screen.getByTestId('workspace-code-text-preview')).toHaveTextContent('export const ok = true;');
    expect(screen.getByText('Workspace 文件浏览器')).toBeInTheDocument();
  });

  it('does not read binary files when selected', async () => {
    render(<WorkspaceFilesPage workspace="/repo" onBackToMain={vi.fn()} />);

    fireEvent.click(await screen.findByText('logo.png'));

    await waitFor(() => {
      expect(screen.getByText('二进制文件不在浏览器中直接读取')).toBeInTheDocument();
    });
    expect(serviceMocks.readScopedFile).toHaveBeenCalledTimes(1);
  });

  it('reloads the tree with ignored directories when vendor cache is enabled', async () => {
    render(<WorkspaceFilesPage workspace="/repo" onBackToMain={vi.fn()} />);

    fireEvent.click(await screen.findByText('vendor/cache'));

    await waitFor(() => {
      expect(serviceMocks.listWorkspaceFileTree).toHaveBeenLastCalledWith(
        expect.objectContaining({ includeIgnored: true }),
      );
    });
  });

  it('ignores stale tree responses after scope changes', async () => {
    const workspaceTree = deferred<{ ok: true; data: WorkspaceFileTreeResponse }>();
    const runtimeTree = deferred<{ ok: true; data: WorkspaceFileTreeResponse }>();
    serviceMocks.listWorkspaceFileTree
      .mockReturnValueOnce(workspaceTree.promise)
      .mockReturnValueOnce(runtimeTree.promise);

    render(<WorkspaceFilesPage workspace="/repo" onBackToMain={vi.fn()} />);

    fireEvent.click(screen.getByText('Runtime'));
    runtimeTree.resolve({
      ok: true,
      data: makeTree({ fileName: 'runtime.log', filePath: 'runtime.log', scope: 'runtime' }),
    });

    await waitFor(() => {
      expect(screen.getAllByText('runtime.log').length).toBeGreaterThan(0);
    });
    workspaceTree.resolve({
      ok: true,
      data: makeTree({ fileName: 'stale.ts', filePath: 'src/stale.ts', scope: 'workspace' }),
    });

    await waitFor(() => {
      expect(screen.queryByText('stale.ts')).not.toBeInTheDocument();
    });
  });
});
