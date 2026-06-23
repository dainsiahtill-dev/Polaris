/**
 * File Service
 *
 * 封装所有文件操作相关的API调用
 */

import { apiGet, buildQueryString } from './apiClient';
import type { ApiResult } from './api.types';
import type { FilePayload, WorkspaceFileTreeOptions, WorkspaceFileTreeResponse } from './api.types';

export type { FilePayload };
export type { WorkspaceFileNode, WorkspaceFileTreeOptions, WorkspaceFileTreeResponse } from './api.types';

// ============================================================================
// File API
// ============================================================================

/**
 * 规范化文件路径
 * 将.polaris/runtime路径转换为runtime路径
 */
export function normalizeArtifactPath(path: string): string {
  const normalized = String(path || '').trim().replace(/\\/g, '/');
  if (!normalized) return normalized;
  if (normalized === '.polaris/runtime') return 'runtime';
  if (normalized.startsWith('.polaris/runtime/')) {
    return `runtime/${normalized.slice('.polaris/runtime/'.length)}`;
  }
  return normalized;
}

/**
 * 读取文件内容
 * @param path 文件路径
 * @param tailLines 可选，读取最后N行
 */
export async function readFile(
  path: string,
  tailLines?: number
): Promise<ApiResult<FilePayload>> {
  const normalizedPath = normalizeArtifactPath(path);
  const query = buildQueryString({
    path: normalizedPath,
    tail_lines: tailLines,
  });

  return apiGet<FilePayload>(`/files/read${query}`, 'Failed to read file');
}

export async function readWorkspaceFile(
  path: string,
  tailLines?: number
): Promise<ApiResult<FilePayload>> {
  return readScopedFile(path, 'workspace', tailLines);
}

export async function readScopedFile(
  path: string,
  scope: 'workspace' | 'runtime' | 'config' = 'workspace',
  tailLines = 0
): Promise<ApiResult<FilePayload>> {
  const readMode = tailLines > 0 ? 'tail' : 'head';
  const query = buildQueryString({
    path: String(path || '').trim(),
    scope,
    tail_lines: tailLines,
    max_chars: 600000,
    read_mode: readMode,
  });

  return apiGet<FilePayload>(`/v2/files/read${query}`, 'Failed to read scoped file');
}

export async function listWorkspaceFileTree(
  options: WorkspaceFileTreeOptions = {},
): Promise<ApiResult<WorkspaceFileTreeResponse>> {
  const query = buildQueryString({
    root: options.root || '',
    scope: options.scope || 'workspace',
    max_depth: options.maxDepth ?? 12,
    max_entries: options.maxEntries ?? 6000,
    include_hidden: options.includeHidden ?? true,
    include_ignored: options.includeIgnored ?? false,
  });

  return apiGet<WorkspaceFileTreeResponse>(`/v2/files/tree${query}`, 'Failed to load workspace file tree');
}

/**
 * 读取日志文件尾部
 * @param path 日志文件路径
 * @param lines 读取行数（默认20）
 */
export async function readLogTail(path: string, lines = 20): Promise<string> {
  const result = await readFile(path, 200);

  if (!result.ok || !result.data?.content) {
    return '';
  }

  const allLines = result.data.content.split('\n');
  return allLines.slice(-lines).join('\n');
}

/**
 * 读取JSON文件并解析
 * @param path JSON文件路径
 * @param tailLines 可选，读取最后N行
 */
export async function readJsonFile<T>(
  path: string,
  tailLines?: number
): Promise<ApiResult<T>> {
  const result = await readFile(path, tailLines);

  if (!result.ok || !result.data) {
    return result as unknown as ApiResult<T>;
  }

  try {
    const parsed = JSON.parse(result.data.content) as T;
    return { ok: true, data: parsed };
  } catch (error) {
    return {
      ok: false,
      error: `Failed to parse JSON: ${error instanceof Error ? error.message : 'Unknown error'}`,
    };
  }
}
