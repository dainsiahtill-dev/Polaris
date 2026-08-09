/**
 * File Service
 *
 * 封装所有文件操作相关的API调用
 */
import { apiGet, buildQueryString } from './apiClient';
// ============================================================================
// File API
// ============================================================================
/**
 * 规范化文件路径
 * 将.polaris/runtime路径转换为runtime路径
 */
export function normalizeArtifactPath(path) {
    const normalized = String(path || '').trim().replace(/\\/g, '/');
    if (!normalized)
        return normalized;
    if (normalized === '.polaris/runtime')
        return 'runtime';
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
export async function readFile(path, tailLines) {
    const normalizedPath = normalizeArtifactPath(path);
    const query = buildQueryString({
        path: normalizedPath,
        tail_lines: tailLines,
    });
    return apiGet(`/v2/files/read${query}`, 'Failed to read file');
}
export async function readWorkspaceFile(path, tailLines) {
    return readScopedFile(path, 'workspace', tailLines);
}
export async function readScopedFile(path, scope = 'workspace', tailLines = 0) {
    const readMode = tailLines > 0 ? 'tail' : 'head';
    const query = buildQueryString({
        path: String(path || '').trim(),
        scope,
        tail_lines: tailLines,
        max_chars: 600000,
        read_mode: readMode,
    });
    return apiGet(`/v2/files/read${query}`, 'Failed to read scoped file');
}
export async function listWorkspaceFileTree(options = {}) {
    const query = buildQueryString({
        root: options.root || '',
        scope: options.scope || 'workspace',
        max_depth: options.maxDepth ?? 12,
        max_entries: options.maxEntries ?? 6000,
        include_hidden: options.includeHidden ?? true,
        include_ignored: options.includeIgnored ?? false,
    });
    return apiGet(`/v2/files/tree${query}`, 'Failed to load workspace file tree');
}
/**
 * 读取日志文件尾部
 * @param path 日志文件路径
 * @param lines 读取行数（默认20）
 */
export async function readLogTail(path, lines = 20) {
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
export async function readJsonFile(path, tailLines) {
    const result = await readFile(path, tailLines);
    if (!result.ok || !result.data) {
        return result;
    }
    try {
        const parsed = JSON.parse(result.data.content);
        return { ok: true, data: parsed };
    }
    catch (error) {
        return {
            ok: false,
            error: `Failed to parse JSON: ${error instanceof Error ? error.message : 'Unknown error'}`,
        };
    }
}
