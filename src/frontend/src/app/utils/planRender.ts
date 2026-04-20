/**
 * 敕令总图(plan.md)渲染工具函数
 * 
 * 功能：
 * normalizePlanText - 规范化被转义的多行文本
 */

/**
 * 规范化被转义的多行文本
 * 当字符串"包含 \\n 且不包含真实换行 \n 且 \\n 数量 >= 2"时执行解码
 */
export function normalizePlanText(text: string): { text: string; normalized: boolean } {
  if (!text || typeof text !== 'string') {
    return { text: text || '', normalized: false };
  }

  const escapedNewlineCount = (text.match(/\\n/g) || []).length;
  const hasRealNewline = text.includes('\n');

  // 只有当包含字面量 \n、不包含真实换行、且 \n 数量 >= 2 时才规范化
  if (escapedNewlineCount < 2 || hasRealNewline) {
    return { text, normalized: false };
  }

  // 执行规范化：仅处理 \r\n、\n、\t
  let normalized = text;
  normalized = normalized.replace(/\\r\\n/g, '\r\n');
  normalized = normalized.replace(/\\n/g, '\n');
  normalized = normalized.replace(/\\t/g, '\t');

  return { text: normalized, normalized: true };
}
