export type FoundationDiagramKind = 'architecture' | 'transaction' | 'context' | 'delivery' | 'none';

export type FoundationTopic = {
  id: string;
  title: string;
  subtitle: string;
  summary: string;
  markdown: string;
  diagram: FoundationDiagramKind;
};

export type FoundationModule = {
  id: string;
  name: string;
  category: '主链机制' | '基础支撑' | '可选能力' | '研究性模块';
  principle: string;
  location: string;
  limitation: string;
  topicId: string;
};

export type FoundationPaper = {
  id: string;
  title: string;
  subtitle: string;
  abstract: string;
  markdown: string;
  filename: string;
};

export type FoundationReference = {
  id: string;
  title: string;
  location: string;
  note: string;
  url?: string;
};

export type FoundationContent = {
  title: string;
  subtitle: string;
  reviewedOn: string;
  basis: string;
  topics: FoundationTopic[];
  modules: FoundationModule[];
  papers: FoundationPaper[];
  references: FoundationReference[];
};
