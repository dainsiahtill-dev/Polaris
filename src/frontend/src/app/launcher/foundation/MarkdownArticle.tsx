import React, { useId, type ReactNode } from 'react';

export function safeArticleHref(href: string): string | undefined {
  const value = href.trim();
  if (/[\u0000-\u0020]/.test(value)) return undefined;
  if (/^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(value)) return value;
  return undefined;
}

function inline(text: string, key: string): ReactNode[] {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)/g;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    const token = match[0];
    const id = `${key}-${start}`;
    if (token.startsWith('`')) nodes.push(<code key={id}>{token.slice(1, -1)}</code>);
    else if (token.startsWith('**')) nodes.push(<strong key={id}>{inline(token.slice(2, -2), id)}</strong>);
    else if (token.startsWith('*')) nodes.push(<em key={id}>{token.slice(1, -1)}</em>);
    else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      const href = link && safeArticleHref(link[2]);
      nodes.push(href && link
        ? <a key={id} href={href} target={/^https?:/i.test(href) ? '_blank' : undefined} rel="noopener noreferrer">{link[1]}</a>
        : <React.Fragment key={id}>{link?.[1] ?? token}</React.Fragment>);
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

const tableRow = (line: string): string[] => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
const isTableRule = (line: string) => /^\s*\|?\s*:?-{3,}/.test(line) && tableRow(line).every(cell => /^:?-{3,}:?$/.test(cell));
const isBlockStart = (line: string) => /^(#{1,6}\s|```|>\s?|[-*+]\s|\d+\.\s|\s*---+\s*$)/.test(line);

/** A text-only Markdown subset. Raw HTML remains text; no DOM HTML injection. */
export const MarkdownArticle = React.memo(function MarkdownArticle({ markdown, headingOffset = 2 }: { markdown: string; headingOffset?: number }) {
  const prefix = useId();
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `${prefix}-${i}`;
    if (!line.trim()) { i += 1; continue; }
    if (line.startsWith('```')) {
      const language = line.slice(3).trim();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++]);
      if (i < lines.length) i += 1;
      blocks.push(<figure key={key} className="foundation-code">{language && <figcaption>{language === 'mermaid' ? '状态定义 · Mermaid 源码' : language}</figcaption>}<pre><code>{code.join('\n')}</code></pre></figure>);
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = Math.min(6, Math.max(1, heading[1].length + headingOffset));
      blocks.push(React.createElement(`h${level}`, { key, id: key }, inline(heading[2], key)));
      i += 1; continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { blocks.push(<hr key={key} />); i += 1; continue; }
    if (i + 1 < lines.length && line.includes('|') && isTableRule(lines[i + 1])) {
      const headers = tableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) rows.push(tableRow(lines[i++]));
      blocks.push(<div className="foundation-table" key={key}><table><thead><tr>{headers.map((cell, j) => <th key={j} scope="col">{inline(cell, `${key}-h${j}`)}</th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}>{headers.map((_, c) => <td key={c}>{inline(row[c] ?? '', `${key}-${j}-${c}`)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ''));
      blocks.push(<blockquote key={key}>{inline(quote.join(' '), key)}</blockquote>); continue;
    }
    const list = /^\s*([-*+]|\d+\.)\s+/.exec(line);
    if (list) {
      const ordered = /\d/.test(list[1]);
      const items: string[] = [];
      const expression = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/;
      while (i < lines.length && expression.test(lines[i])) items.push(lines[i++].replace(expression, ''));
      const entries = items.map((item, j) => <li key={j}>{inline(item, `${key}-${j}`)}</li>);
      blocks.push(ordered ? <ol key={key}>{entries}</ol> : <ul key={key}>{entries}</ul>); continue;
    }
    const paragraph: string[] = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i]) && !(i + 1 < lines.length && isTableRule(lines[i + 1]))) paragraph.push(lines[i++]);
    blocks.push(<p key={key}>{inline(paragraph.join(' '), key)}</p>);
  }
  return <div className="foundation-markdown">{blocks}</div>;
});
