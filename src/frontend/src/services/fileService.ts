/**
 * File Service
 *
 * 封装所有文件操作相关的API调用
 */

import { apiGet, buildQueryString } from './apiClient';
import type { ApiResult } from './api.types';
import type { FilePayload } from './api.types';

export type { FilePayload };

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
