import { describe, expect, it } from 'vitest';

import { normalizeDocsInitPreviewPayload } from './docsInitPreview';

describe('normalizeDocsInitPreviewPayload', () => {
  it('accepts a valid preview payload', () => {
    const payload = normalizeDocsInitPreviewPayload({
      mode: 'minimal',
      target_root: 'workspace/docs',
      docs_exists: false,
      files: [
        {
          path: 'workspace/docs/product/plan.md',
          content: '# Plan',
          exists: false,
        },
      ],
      project: { name: 'fileserver' },
    });

    expect(payload).toEqual({
      mode: 'minimal',
      target_root: 'workspace/docs',
      docs_exists: false,
      files: [
        {
          path: 'workspace/docs/product/plan.md',
          content: '# Plan',
          exists: false,
        },
      ],
      project: { name: 'fileserver' },
    });
  });

  it('rejects payloads without a valid files array', () => {
    expect(
      normalizeDocsInitPreviewPayload({
        mode: 'minimal',
        target_root: 'workspace/docs',
        files: undefined,
      }),
    ).toBeNull();

    expect(
      normalizeDocsInitPreviewPayload({
        mode: 'minimal',
        target_root: 'workspace/docs',
        files: [{ content: '# Missing path' }],
      }),
    ).toBeNull();
  });

  it('filters malformed file entries but keeps valid ones', () => {
    const payload = normalizeDocsInitPreviewPayload({
      mode: 'minimal',
      target_root: 'workspace/docs',
      docs_exists: true,
      files: [
        null,
        { path: '', content: 'bad' },
        { path: 'workspace/docs/product/requirements.md', content: 123 },
      ],
    });

    expect(payload).toEqual({
      mode: 'minimal',
      target_root: 'workspace/docs',
      docs_exists: true,
      files: [
        {
          path: 'workspace/docs/product/requirements.md',
          content: '123',
          exists: undefined,
        },
      ],
      project: undefined,
    });
  });
});
