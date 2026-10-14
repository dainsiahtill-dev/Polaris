import { createElement } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MarkdownArticle } from './MarkdownArticle';

describe('safe technical article rendering', () => {
  it('renders headings, code, table evidence and lists as readable semantic elements', () => {
    const { container } = render(createElement(MarkdownArticle, { markdown: '# 协议\n\n**原始事实** 与 `receipt`。\n\n| 层 | 权威 |\n| --- | --- |\n| Turn | CommitReceipt |\n\n- 真实执行\n- 独立验收\n\n```python\nassert effect != verdict\n```' }));
    expect(screen.getByRole('heading', { name: '协议', level: 3 })).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveTextContent('CommitReceipt');
    expect(screen.getByRole('list')).toHaveTextContent('独立验收');
    expect(container.querySelector('pre code')).toHaveTextContent('assert effect != verdict');
    expect(container.querySelector('strong')).toHaveTextContent('原始事实');
  });
  it('keeps raw HTML and executable links inert while retaining safe references', () => {
    const { container } = render(createElement(MarkdownArticle, { markdown: '<img src=x onerror=alert(1)>\n\n[危险](javascript:alert) [证据](https://example.org/paper) [伪相对](//example.org)' }));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.queryByRole('link', { name: '危险' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '伪相对' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '证据' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>');
  });
});
