import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, Download, Layers3, Search, Printer, ExternalLink, FileText } from 'lucide-react';
import { foundationContent } from './content';
import type { FoundationContent, FoundationDiagramKind } from './schema';
import { FoundationDiagrams } from './FoundationDiagrams';
import { MarkdownArticle, safeArticleHref } from './MarkdownArticle';
import './foundation.css';

const paperDiagrams: Record<string, FoundationDiagramKind> = {
  system: 'architecture',
  'transaction-turn': 'transaction',
  contextos: 'context',
  'control-and-delivery': 'delivery',
};

const categories = ['全部', '主链机制', '基础支撑', '可选能力', '研究性模块'] as const;
const specialTopics = [
  { id: 'modules', title: '模块图谱', icon: Layers3 },
  { id: 'papers', title: '论文集', icon: FileText },
  { id: 'references', title: '源码与参考', icon: ExternalLink },
];

function downloadMarkdown(filename: string, markdown: string) {
  const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  try { link.click(); } finally { link.remove(); URL.revokeObjectURL(url); }
}

export function FoundationWorkspace({ topicId, onNavigateTopic, onExit, content = foundationContent }: {
  topicId?: string;
  onNavigateTopic: (topicId: string) => void;
  onExit: () => void;
  content?: FoundationContent;
}) {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${content.title} · Polaris Launcher`;
    return () => { document.title = previousTitle; };
  }, [content.title]);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string>('全部');
  const [paperId, setPaperId] = useState('');
  const articleRef = useRef<HTMLElement>(null);
  const query = search.trim().toLocaleLowerCase();
  const topics = useMemo(() => content.topics.filter(topic => `${topic.title} ${topic.subtitle} ${topic.summary}`.toLocaleLowerCase().includes(query)), [content.topics, query]);
  const modules = useMemo(() => content.modules.filter(module => (category === '全部' || module.category === category) && `${module.name} ${module.principle} ${module.location} ${module.limitation}`.toLocaleLowerCase().includes(query)), [category, content.modules, query]);
  const references = useMemo(() => content.references.filter(reference => `${reference.id} ${reference.title} ${reference.location} ${reference.note}`.toLocaleLowerCase().includes(query)), [content.references, query]);
  const topic = content.topics.find(item => item.id === topicId) ?? content.topics[0];
  const special = specialTopics.find(item => item.id === topicId);
  const selectedPaper = content.papers.find(paper => paper.id === paperId) ?? content.papers[0];
  const navigate = (id: string) => {
    onNavigateTopic(id);
    articleRef.current?.scrollTo?.({ top: 0, behavior: 'instant' });
    articleRef.current?.focus();
  };

  return <div className="foundation-workspace">
    <div className="foundation-screen">
      <a className="foundation-skip" href="#foundation-article">跳至正文</a>
      <header className="foundation-header">
        <div className="foundation-brand"><BookOpen size={22} aria-hidden="true" /><div><h1>Polaris 底座原理</h1><p>{content.subtitle}</p></div></div>
        <button type="button" className="foundation-button" onClick={onExit}><ArrowLeft size={16} aria-hidden="true" />返回实例总控</button>
      </header>
      <div className="foundation-layout">
        <aside className="foundation-sidebar" aria-label="原理研究导航">
          <label className="foundation-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="搜索原理主题与模块" placeholder="搜索原理、模块、源码…" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <nav aria-label="机制阅读"><p className="foundation-nav-label">机制阅读</p>
            {topics.map(item => <button type="button" className="foundation-topic-link" key={item.id} aria-label={`${item.title} ${item.subtitle}`} aria-current={!special && topic?.id === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><span>{item.title}</span><small>{item.subtitle}</small></button>)}
            {topics.length === 0 && <p className="foundation-empty">没有匹配主题。试试“事务”或“上下文”。</p>}
          </nav>
          <nav className="foundation-resource-nav" aria-label="研究资料">
            {specialTopics.map(item => <button type="button" key={item.id} aria-current={topicId === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon size={16} aria-hidden="true" />{item.title}</button>)}
          </nav>
          <div className="foundation-basis"><strong>阅读依据</strong><p>{content.basis}</p><span>源码核对 {content.reviewedOn}</span></div>
        </aside>
        <main className="foundation-reader" ref={articleRef} id="foundation-article" tabIndex={-1}>
          {special?.id === 'modules' ? <>
            <div className="foundation-article-heading"><span>能力归属与实现边界</span><h2>模块图谱</h2><p>按能力类别查看原理、源码落点与当前限制。</p></div>
            <div className="foundation-category-filter" role="group" aria-label="模块类别">{categories.map(item => <button type="button" key={item} aria-pressed={category === item} onClick={() => setCategory(item)}>{item}</button>)}</div>
            <div className="foundation-module-list">{modules.map(module => <section key={module.id} className="foundation-module"><div className="foundation-module-title"><h3>{module.name}</h3><span>{module.category}</span></div><p>{module.principle}</p><code>{module.location}</code><p className="foundation-limitation"><strong>边界</strong> {module.limitation}</p><button type="button" className="foundation-text-link" onClick={() => navigate(module.topicId)}>阅读 {content.topics.find(item => item.id === module.topicId)?.title ?? module.topicId}<span aria-hidden="true"> ↗</span></button></section>)}</div>
            {modules.length === 0 && <p className="foundation-empty">当前类别没有匹配模块。</p>}
          </> : special?.id === 'papers' ? <>
            <div className="foundation-article-heading"><span>系统论文与机制专题</span><h2>论文集</h2><p>完整稿件基于源码分析；不将设计目标写成未经验证的实验结果。</p></div>
            <div className="foundation-paper-actions"><button className="foundation-button" type="button" onClick={() => window.print()}><Printer size={16} aria-hidden="true" />打印完整论文集 / 保存 PDF</button></div>
            <div className="foundation-paper-index" role="group" aria-label="选择论文">{content.papers.map(paper => <button type="button" key={paper.id} aria-label={`${paper.title} ${paper.subtitle}`} aria-pressed={selectedPaper?.id === paper.id} onClick={() => setPaperId(paper.id)}><strong>{paper.title}</strong><span>{paper.subtitle}</span></button>)}</div>
            {selectedPaper && <section className="foundation-paper"><h3>{selectedPaper.title}</h3><p className="foundation-paper-abstract">{selectedPaper.abstract}</p><button className="foundation-button" type="button" aria-label={`下载 ${selectedPaper.title} Markdown`} onClick={() => downloadMarkdown(selectedPaper.filename, selectedPaper.markdown)}><Download size={16} aria-hidden="true" />下载 Markdown</button><MarkdownArticle markdown={selectedPaper.markdown} /></section>}
          </> : special?.id === 'references' ? <>
            <div className="foundation-article-heading"><span>可追溯的实现与研究依据</span><h2>源码与参考</h2><p>源码位置说明实现归属；外部文献提供概念参照，不替代 Polaris 的运行证据。</p></div>
            <ol className="foundation-reference-list">{references.map(ref => <li key={ref.id}><code className="foundation-reference-id">{ref.id}</code><h3>{ref.title}</h3><code>{ref.location}</code><p>{ref.note}</p>{ref.url && safeArticleHref(ref.url) && <a href={safeArticleHref(ref.url)} target="_blank" rel="noopener noreferrer">阅读原始来源 <ExternalLink size={14} aria-hidden="true" /></a>}</li>)}</ol>
            {references.length === 0 && <p className="foundation-empty">没有匹配来源。可搜索证据编号、源码路径或符号。</p>}
          </> : topic ? <>
            <div className="foundation-article-heading"><span>{topic.subtitle}</span><h2>{topic.title}</h2><p>{topic.summary}</p></div>
            <FoundationDiagrams key={topic.id} kind={topic.diagram} />
            <MarkdownArticle markdown={topic.markdown} />
          </> : <p className="foundation-empty">研究内容正在整理。</p>}
          <footer className="foundation-reading-footer">源码解释与运行验收分开 · 当前页面不执行任务或修改项目</footer>
        </main>
      </div>
    </div>
    <section className="foundation-print" data-testid="foundation-paper-print" aria-hidden="true">
      <header><h1>{content.title} · 论文集</h1><p>{content.subtitle}</p><p>源码核对：{content.reviewedOn} · {content.basis}</p></header>
      {content.papers.map(paper => <article className="foundation-print-paper" key={paper.id}><h2>{paper.title}</h2><p>{paper.subtitle}</p><p>{paper.abstract}</p><FoundationDiagrams kind={paperDiagrams[paper.id] ?? 'none'} staticMode /><MarkdownArticle markdown={paper.markdown} /></article>)}
    </section>
  </div>;
}
