import { createElement } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { FoundationContent } from './schema';
import { FoundationWorkspace } from './FoundationWorkspace';

const content: FoundationContent = {
  title: 'Polaris 底座原理', subtitle: '角色、事务和证据的边界', reviewedOn: '2026-10-14', basis: '源码研究，非运行验收',
  topics: [
    { id: 'transaction', title: '事务轮', subtitle: 'TransactionTurn', summary: '效果与提交分开', markdown: '## 效果边界\n\n工具完成不是项目完成。', diagram: 'transaction' },
    { id: 'contextos', title: 'ContextOS', subtitle: '最终请求', summary: 'provider body', markdown: '原始事实及覆盖证据。', diagram: 'context' },
  ],
  modules: [
    { id: 'kernel', name: '角色内核', category: '主链机制', principle: '单轮执行', location: 'roles/kernel', limitation: '不是项目 verdict', topicId: 'transaction' },
    { id: 'research', name: '认知研究', category: '研究性模块', principle: '建议', location: 'cognitive', limitation: '没有执行权', topicId: 'contextos' },
  ],
  papers: [
    { id: 'system', title: '系统论文', subtitle: '草稿', abstract: '执行与验收', markdown: '# 系统论文\n\n全篇第一章。', filename: 'system.md' },
    { id: 'transaction-turn', title: '事务专题', subtitle: '草稿', abstract: '副作用', markdown: '# 事务专题\n\n完整第二篇。', filename: 'transaction.md' },
  ],
  references: [{ id: 'source', title: '源码', location: 'roles/kernel:62', note: '执行边界' }],
};

afterEach(() => vi.restoreAllMocks());

describe('foundation technical reader', () => {
  it('sets the document and PDF title while reading and restores the prior workspace title', () => {
    const originalTitle = document.title;
    document.title = '原工作台';
    const { unmount } = render(createElement(FoundationWorkspace, { content, topicId: 'papers', onNavigateTopic: vi.fn(), onExit: vi.fn() }));
    expect(document.title).toBe('Polaris 底座原理 · Polaris Launcher');
    unmount();
    expect(document.title).toBe('原工作台');
    document.title = originalTitle;
  });
  it('opens the URL topic and searches the topic index without changing execution state', () => {
    const navigate = vi.fn();
    render(createElement(FoundationWorkspace, { content, topicId: 'contextos', onNavigateTopic: navigate, onExit: vi.fn() }));
    expect(screen.getByRole('heading', { name: 'ContextOS', level: 2 })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索原理主题与模块' }), { target: { value: '事务' } });
    expect(screen.getByRole('button', { name: /事务轮 TransactionTurn/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /ContextOS 最终请求/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /事务轮 TransactionTurn/ }));
    expect(navigate).toHaveBeenCalledWith('transaction');
  });
  it('filters modules and links them to the owning mechanism', () => {
    const navigate = vi.fn();
    render(createElement(FoundationWorkspace, { content, topicId: 'modules', onNavigateTopic: navigate, onExit: vi.fn() }));
    fireEvent.click(screen.getByRole('button', { name: '研究性模块' }));
    expect(screen.getByRole('heading', { name: '认知研究' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '角色内核' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /阅读 ContextOS/ }));
    expect(navigate).toHaveBeenCalledWith('contextos');
  });
  it('searches source references by their evidence identifier and reports no match', () => {
    render(createElement(FoundationWorkspace, { content: { ...content, references: [...content.references, { id: 'other', title: '其他证据', location: 'control/ledger', note: '独立 owner' }] }, topicId: 'references', onNavigateTopic: vi.fn(), onExit: vi.fn() }));
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索原理主题与模块' }), { target: { value: 'source' } });
    expect(screen.getByRole('heading', { name: '源码' })).toBeInTheDocument();
    expect(screen.getByText('source')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '其他证据' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索原理主题与模块' }), { target: { value: 'unknown-ref' } });
    expect(screen.getByText('没有匹配来源。可搜索证据编号、源码路径或符号。')).toBeInTheDocument();
  });
  it('exposes the durable commit boundary after in-memory completed', () => {
    render(createElement(FoundationWorkspace, { content, topicId: 'transaction', onNavigateTopic: vi.fn(), onExit: vi.fn() }));
    fireEvent.click(screen.getByRole('button', { name: /OutcomeCommit/ }));
    expect(screen.getByRole('region', { name: '节点边界说明' })).toHaveTextContent('COMPLETED');
    expect(screen.getByRole('region', { name: '节点边界说明' })).toHaveTextContent('不等于项目验收');
    expect(within(screen.getByRole('main')).getByText(/静态原理示意/)).toBeInTheDocument();
  });
  it('prints every complete paper and downloads the selected Markdown bytes', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    const objectUrl = vi.fn((value: Blob) => { void value; return 'blob:paper'; });
    Object.defineProperty(URL, 'createObjectURL', { value: objectUrl, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const { container } = render(createElement(FoundationWorkspace, { content, topicId: 'papers', onNavigateTopic: vi.fn(), onExit: vi.fn() }));
    const printed = container.querySelector('[data-testid="foundation-paper-print"]');
    expect(printed).toHaveTextContent('全篇第一章');
    expect(printed).toHaveTextContent('完整第二篇');
    expect(printed).toHaveTextContent('OutcomeCommit');
    expect(printed).toHaveTextContent('静态原理示意 · 非运行测量');
    fireEvent.click(screen.getByRole('button', { name: '打印完整论文集 / 保存 PDF' }));
    expect(print).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '下载 系统论文 Markdown' }));
    expect(objectUrl.mock.calls[0][0]).toBeInstanceOf(Blob);
    const bytes = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(objectUrl.mock.calls[0][0]); });
    expect(bytes).toBe(content.papers[0].markdown);
    expect(click).toHaveBeenCalledOnce();
  });
});
