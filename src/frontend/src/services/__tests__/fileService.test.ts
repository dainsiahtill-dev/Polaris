import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listWorkspaceFileTree, readScopedFile, readWorkspaceFile } from '../fileService';

const mockApiGet = vi.hoisted(() => vi.fn());

vi.mock('../apiClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../apiClient')>()),
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

describe('fileService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiGet.mockResolvedValue({ ok: true, data: {} });
  });

  it('reads scoped files without the legacy 400-line tail limit by default', async () => {
    await readScopedFile('src/large.ts', 'workspace');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/files/read?path=src%2Flarge.ts&scope=workspace&tail_lines=0&max_chars=600000&read_mode=head',
      'Failed to read scoped file',
    );
  });

  it('lets explicit tail line limits override full-preview mode', async () => {
    await readWorkspaceFile('runtime/log.txt', 200);

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/files/read?path=runtime%2Flog.txt&scope=workspace&tail_lines=200&max_chars=600000&read_mode=tail',
      'Failed to read scoped file',
    );
  });

  it('builds a bounded file tree query with ignored directories disabled by default', async () => {
    await listWorkspaceFileTree({ scope: 'runtime', maxDepth: 4, maxEntries: 25 });

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/files/tree?root=&scope=runtime&max_depth=4&max_entries=25&include_hidden=true&include_ignored=false',
      'Failed to load workspace file tree',
    );
  });
});
