export interface DocsInitFile {
  path: string;
  content: string;
  exists?: boolean;
}

export interface DocsInitPreview {
  mode: string;
  target_root: string;
  docs_exists: boolean;
  files: DocsInitFile[];
  project?: Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function normalizeFile(value: unknown): DocsInitFile | null {
  const record = asRecord(value);
  if (!record) {
    return null;
  }

  const path = asString(record.path).trim();
  if (!path) {
    return null;
  }

  return {
    path,
    content: typeof record.content === 'string' ? record.content : String(record.content ?? ''),
    exists: typeof record.exists === 'boolean' ? record.exists : undefined,
  };
}

export function normalizeDocsInitPreviewPayload(value: unknown): DocsInitPreview | null {
  const record = asRecord(value);
  if (!record) {
    return null;
  }

  const targetRoot = asString(record.target_root).trim();
  if (!targetRoot) {
    return null;
  }

  const rawFiles = Array.isArray(record.files) ? record.files : [];
  const files = rawFiles
    .map((item) => normalizeFile(item))
    .filter((item): item is DocsInitFile => item !== null);
  if (files.length === 0) {
    return null;
  }

  const project = asRecord(record.project) ?? undefined;

  return {
    mode: asString(record.mode).trim() || 'minimal',
    target_root: targetRoot,
    docs_exists: Boolean(record.docs_exists),
    files,
    project,
  };
}
