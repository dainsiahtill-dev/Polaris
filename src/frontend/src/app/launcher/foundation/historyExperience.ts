import historyExperienceMarkdown from './papers/04-history-experience-report.zh.md?raw';
import type { FoundationPaper, FoundationTopic } from './schema';

const chapterLabels = [
  ['阅读结论', '核心认识、证据等级与阅读方式'],
  ['历代演进', '从早期架构到近期正式交付实验'],
  ['架构经验', '角色、owner、工具、上下文与实例边界'],
  ['踩坑案例', '68 个案例：早期 12、中期 26、近期 30'],
  ['验证边界', 'r44/r48、R10 与十月 M01—M16 的实际范围'],
  ['研发方法', '真实接口、可证伪实验、独立复核与冻结验收'],
  ['文档阅读', '1,285 份材料索引、23 组副本与状态漂移'],
  ['后续顺序', '当前模块闭合、正式新运行、重复性与经济性'],
  ['验收交接', '结论写法、机器缺陷记录与交接模板'],
  ['最终认识与来源', '67 份引用原件、行号摘录、下载与机器目录'],
] as const;

const chapters = historyExperienceMarkdown
  .split(/(?=^## \d+\. )/m)
  .slice(1);

export const historyExperienceTopics: FoundationTopic[] = [
  {
    id: 'history-experience-report',
    title: '历史经验报告',
    subtitle: '2026-10-20 原报告 · 含 2026-10-25 证据续记',
    summary: '保留历代演进、68 个案例与原验证边界；补记 2026-10-25 L2-17 业务、原生、UI 与缓存证据，分别标注通过和未闭合。',
    markdown: [
      '**分章阅读**',
      ...chapterLabels.map(([label], index) => `- [${String(index + 1).padStart(2, '0')} ${label}](/launcher?view=foundation&topic=history-experience-${index + 1})`),
      '',
      '[下载完整报告 Markdown](/reports/polaris-history-experience.md) · [来源摘录](/reports/polaris-history-sources.html) · [完整文档目录](/reports/polaris-history-document-catalogue.md)',
      '',
      historyExperienceMarkdown,
    ].join('\n'),
    diagram: 'none',
  },
  ...chapterLabels.map(([label, summary], index): FoundationTopic => ({
    id: `history-experience-${index + 1}`,
    title: `历史 · ${String(index + 1).padStart(2, '0')} ${label}`,
    subtitle: '历史经验报告 · 分章阅读',
    summary,
    markdown: `[返回完整报告](/launcher?view=foundation&topic=history-experience-report)\n\n${chapters[index]}`,
    diagram: 'none',
  })),
];

export const historyExperiencePaper: FoundationPaper = {
  id: 'history-experience-report',
  title: '历史经验报告',
  subtitle: '历代经验与验证边界 · 含 2026-10-25 证据续记',
  abstract: '保留原报告 68 个案例与历史验证边界，追加 2026-10-25 L2-17 的实际业务与原生证据、UI／缓存未闭合和 UNKNOWN；不以文档整理替代交付验收。',
  markdown: historyExperienceMarkdown,
  filename: '历史经验报告.md',
};
