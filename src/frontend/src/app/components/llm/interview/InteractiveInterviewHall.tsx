import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, AlertTriangle, Loader2, Send, RefreshCw, Check, XCircle, Maximize2, Minimize2, Brain } from 'lucide-react';
import type { InterviewProviderSummary, InterviewRoleSummary } from './InterviewHall';
import type { TestEvent } from '../test/types';
import { RealtimeThinkingDisplay } from './RealtimeThinkingDisplay';
import { StreamingTags } from './StreamingTags';
import { useInterviewStream, type RealtimeThinkingEvent, type StreamingTagEvent } from './useInterviewStream';
import { resolveModelName, validateModelName, type ModelResolutionContext, type ModelResolutionResult } from '../utils';

type RoleId = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';

const MODEL_FALLBACKS: Record<string, string> = {
  'openai': 'gpt-4',
  'openai_compat': 'gpt-4',
  'anthropic': 'claude-3-sonnet-20240229',
  'anthropic_compat': 'claude-3-sonnet-20240229',
  'kimi': 'kimi-k2-thinking-turbo',
  'minimax': 'abab6.5-chat',
  'gemini_api': 'gemini-1.5-pro',
  'ollama': 'llama2',
  'codex_cli': 'gpt-4-codex',
  'codex_sdk': 'gpt-4',
  'gemini_cli': 'gemini-1.5-pro',
  'custom_https': 'gpt-4',
};

function resolveSelectedModel(
  selectedModel: string | null,
  providerType?: string,
  activeProviderModel?: string
): ModelResolutionResult {
  if (selectedModel && selectedModel.trim()) {
    return {
      model: selectedModel.trim(),
      source: 'role_config',
      isValid: true
    };
  }

  if (activeProviderModel && activeProviderModel.trim()) {
    return {
      model: activeProviderModel.trim(),
      source: 'provider_config',
      isValid: true
    };
  }

  if (providerType) {
    const fallbackModel = MODEL_FALLBACKS[providerType];
    if (fallbackModel) {
      return {
        model: fallbackModel,
        source: 'hardcoded_fallback',
        isValid: true,
        warning: `使用默认模型 ${fallbackModel}`
      };
    }
  }

  return {
    model: 'gpt-4',
    source: 'hardcoded_fallback',
    isValid: false,
    warning: '无法确定模型，改用通用兜底模型'
  };
}

export interface QuestionTemplate {
  id: string;
  category: string;
  title: string;
  question: string;
  expectedCriteria: string[];
  difficulty: 'basic' | 'intermediate' | 'advanced';
  role: RoleId;
}

export interface InterviewMessage {
  id: string;
  type: 'question' | 'answer' | 'system';
  content: string;
  timestamp: string;
  sender: 'user' | 'model';
  questionId?: string;
  question?: string;
  expectedCriteria?: string[];
  thinking?: string;
  evaluation?: {
    userRating: 'pass' | 'fail' | 'pending';
    notes?: string;
    criteriaAssessment?: Record<string, boolean>;
  };
}

export interface InteractiveInterviewReport {
  id: string;
  role: RoleId;
  provider: {
    id: string;
    name: string;
    model: string;
  };
  startTime: string;
  endTime: string;
  overallStatus: 'passed' | 'failed';
  questions: Array<{
    question: string;
    answer: string;
    evaluation?: InterviewMessage['evaluation'];
    expectedCriteria?: string[];
  }>;
  summary: {
    totalQuestions: number;
    passedQuestions: number;
    averageRating: number;
    strengths: string[];
    weaknesses: string[];
    recommendation: string;
  };
  userNotes: string;
}

export interface InteractiveInterviewAnswer {
  sessionId: string;
  answer: string;
  output?: string;
  thinking?: string;
  latencyMs?: number;
  ok?: boolean;
  error?: string | null;
  debug?: {
    prompt?: string;
    cli_args?: string[] | null;
    cli_send_prompt?: boolean | null;
    stdin_prompt?: string | null;
    cli_command?: string | null;
    debug_steps?: string[];
    debug_stream_output?: string[];
  };
}

interface InteractiveInterviewHallProps {
  roles: InterviewRoleSummary[];
  providers: InterviewProviderSummary[];
  selectedRole: RoleId | null;
  selectedProvider: string | null;
  selectedModel: string | null;
  onSelectRole: (role: RoleId) => void;
  onSelectProvider: (providerId: string) => void;
  onAskQuestion: (payload: {
    roleId: RoleId;
    providerId: string;
    model: string;
    question: string;
    expectedCriteria?: string[];
    expectsThinking?: boolean;
    sessionId?: string | null;
    context?: Array<{ question: string; answer: string }>;
  }) => Promise<InteractiveInterviewAnswer | null>;
  onSaveReport: (payload: {
    roleId: RoleId;
    providerId: string;
    model: string | null;
    report: InteractiveInterviewReport;
  }) => Promise<{ saved: boolean; report_path?: string } | null>;
  resolveEnvOverrides?: (providerId: string) => Promise<Record<string, string> | null>;
  onTestEvent?: (event: TestEvent) => void;
  onResetTestEvents?: () => void;
  onSyncTestPanelState?: (payload: {
    providerId: string;
    roleId: RoleId;
    model: string | null;
    status: 'idle' | 'running' | 'success' | 'failed';
  }) => void;
  isDeepTestMode?: boolean;
}

const ROLE_BADGES: Record<string, string> = {
  pm: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
  director: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  qa: 'bg-blue-500/20 text-blue-200 border-blue-500/30',
  architect: 'bg-amber-500/20 text-amber-200 border-amber-500/30'
};

const STATUS_STYLES: Record<string, { border: string; bg: string; dot: string; text: string }> = {
  ready: {
    border: 'border-emerald-500/40',
    bg: 'bg-emerald-500/10',
    dot: 'bg-emerald-400',
    text: 'text-emerald-300'
  },
  failed: {
    border: 'border-rose-500/40',
    bg: 'bg-rose-500/10',
    dot: 'bg-rose-400',
    text: 'text-rose-300'
  },
  testing: {
    border: 'border-cyan-500/40',
    bg: 'bg-cyan-500/10',
    dot: 'bg-cyan-300',
    text: 'text-cyan-200'
  },
  untested: {
    border: 'border-white/10',
    bg: 'bg-white/5',
    dot: 'bg-white/40',
    text: 'text-text-dim'
  }
};

const STATUS_LABELS: Record<string, string> = {
  ready: '连通正常',
  failed: '连通失败',
  testing: '连通测试中',
  untested: '连通未测'
};

const SESSION_STATUS: Record<'idle' | 'running' | 'success' | 'failed', { label: string; badge: string }> = {
  idle: { label: '待命', badge: 'bg-gray-500/20 text-gray-300 border-gray-500/30' },
  running: { label: '进行中', badge: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30' },
  success: { label: '完成', badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30' },
  failed: { label: '失败', badge: 'bg-red-500/20 text-red-200 border-red-500/30' }
};

const QUESTION_TEMPLATES: QuestionTemplate[] = [
  {
    id: 'pm-project-analysis',
    category: '项目规划类',
    title: '项目需求分析',
    question: '请分析这个项目需求并制定实施计划，包括时间安排、资源分配和风险评估。',
    expectedCriteria: ['分析深度', '计划完整性', '风险评估'],
    difficulty: 'intermediate',
    role: 'pm'
  },
  {
    id: 'pm-conflict-resolution',
    category: '冲突协调类',
    title: '技术分歧协调',
    question: '开发团队在前端技术选型上出现分歧，作为PM你如何协调解决？请说明具体步骤和考虑因素。',
    expectedCriteria: ['思考过程', '解决方案', '沟通策略'],
    difficulty: 'advanced',
    role: 'pm'
  },
  {
    id: 'director-architecture',
    category: '架构决策类',
    title: '架构方案选择',
    question: '如果需要在稳定性和交付速度之间权衡，你会如何做架构决策？请给出判断依据。',
    expectedCriteria: ['技术分析', '权衡取舍', '风险评估'],
    difficulty: 'advanced',
    role: 'director'
  },
  {
    id: 'director-code-review',
    category: '代码审查类',
    title: '代码质量改进',
    question: '请说明你在代码审查中如何发现高风险问题，并提出改进建议。',
    expectedCriteria: ['问题识别', '改进方案', '质量标准'],
    difficulty: 'intermediate',
    role: 'director'
  },
  {
    id: 'qa-test-strategy',
    category: '测试策略类',
    title: '测试计划制定',
    question: '面对一个迭代频繁的项目，你会如何制定测试策略以确保质量？',
    expectedCriteria: ['测试覆盖', '风险识别', '执行策略'],
    difficulty: 'intermediate',
    role: 'qa'
  },
  {
    id: 'qa-defect-analysis',
    category: '缺陷分析类',
    title: '线上故障复盘',
    question: '线上出现严重缺陷时，你会如何定位原因并推动修复？',
    expectedCriteria: ['问题定位', '根因分析', '协作推进'],
    difficulty: 'advanced',
    role: 'qa'
  },
  {
    id: 'architect-guide',
    category: '文档编写类',
    title: '功能说明文档',
    question: '请为一个新功能编写简明的使用说明，包含前置条件与操作步骤。',
    expectedCriteria: ['文档完整性', '表达清晰度', '可操作性'],
    difficulty: 'basic',
    role: 'architect'
  },
  {
    id: 'architect-onboarding',
    category: '用户引导类',
    title: '快速上手指南',
    question: '你会如何设计一个新用户的快速上手指南？请说明结构与重点。',
    expectedCriteria: ['结构设计', '用户视角', '示例准确性'],
    difficulty: 'intermediate',
    role: 'architect'
  }
];

const createMessageId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const normalizeCriteriaAssessment = (
  criteria: string[],
  current?: Record<string, boolean>
) => {
  const next: Record<string, boolean> = { ...(current || {}) };
  criteria.forEach((item) => {
    if (typeof next[item] !== 'boolean') {
      next[item] = false;
    }
  });
  return next;
};

export function InteractiveInterviewHall({
  roles,
  providers,
  selectedRole,
  selectedProvider,
  selectedModel,
  onSelectRole,
  onSelectProvider,
  onAskQuestion,
  onSaveReport,
  resolveEnvOverrides,
  onTestEvent,
  onResetTestEvents,
  onSyncTestPanelState,
  isDeepTestMode = false
}: InteractiveInterviewHallProps) {
  const [messages, setMessages] = useState<InterviewMessage[]>([]);
  const [customQuestion, setCustomQuestion] = useState('');
  const [quickQuestion, setQuickQuestion] = useState('');
  const [responding, setResponding] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [view, setView] = useState<'interview' | 'report'>('interview');
  const [report, setReport] = useState<InteractiveInterviewReport | null>(null);
  const [reportSavedPath, setReportSavedPath] = useState<string | null>(null);
  const [userNotes, setUserNotes] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sessionStatus, setSessionStatus] = useState<'idle' | 'running' | 'success' | 'failed'>('idle');
  const [thinkingEvents, setThinkingEvents] = useState<RealtimeThinkingEvent[]>([]);
  const [tagEvents, setTagEvents] = useState<StreamingTagEvent[]>([]);
  const [useStreamingMode, setUseStreamingMode] = useState(true); // Enable streaming by default
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [showTemplatePanel, setShowTemplatePanel] = useState(false);
  const [streamingThinking, setStreamingThinking] = useState('');
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [isThinkingActive, setIsThinkingActive] = useState(false);
  const [isAnswerActive, setIsAnswerActive] = useState(false);
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const pushSessionEvent = useCallback((event: TestEvent) => {
    onTestEvent?.(event);
  }, [onTestEvent]);
  const syncTestPanelStatus = useCallback((status: 'idle' | 'running' | 'success' | 'failed') => {
    if (!selectedRole || !selectedProvider) return;
    onSyncTestPanelState?.({
      providerId: selectedProvider,
      roleId: selectedRole,
      model: selectedModel,
      status
    });
  }, [onSyncTestPanelState, selectedModel, selectedProvider, selectedRole]);
  const handleThinkingEvent = useCallback((event: RealtimeThinkingEvent) => {
    setThinkingEvents((prev) => {
      const next = [...prev];
      const existingIndex = next.findIndex(
        (item) => item.id === event.id && item.kind === event.kind
      );
      if (existingIndex >= 0) {
        next[existingIndex] = { ...next[existingIndex], ...event };
        return next;
      }
      next.push(event);
      const maxEvents = 200;
      if (next.length <= maxEvents) return next;
      return next.slice(next.length - maxEvents);
    });
    if (event.kind === 'reasoning' && event.text) {
      setStreamingThinking((prev) => (prev ? prev + '\n' : '') + event.text);
      setIsThinkingActive(true);
    }
  }, []);
  const clearThinkingEvents = useCallback(() => setThinkingEvents([]), []);
  const handleTagEvent = useCallback((event: StreamingTagEvent) => {
    setTagEvents((prev) => {
      const next = [...prev, event];
      const maxEvents = 500;
      if (next.length <= maxEvents) return next;
      return next.slice(next.length - maxEvents);
    });
    switch (event.type) {
      case 'thinking_start':
        setIsThinkingActive(true);
        setStreamingThinking('');
        break;
      case 'thinking_chunk':
        if (event.data.content) {
          setStreamingThinking((prev) => prev + event.data.content);
        }
        break;
      case 'thinking_end':
        setIsThinkingActive(false);
        break;
      case 'answer_start':
        setIsAnswerActive(true);
        setStreamingAnswer('');
        break;
      case 'answer_chunk':
        if (event.data.content) {
          setStreamingAnswer((prev) => prev + event.data.content);
        }
        break;
      case 'answer_end':
        setIsAnswerActive(false);
        break;
    }
  }, []);
  const clearTagEvents = useCallback(() => {
    setTagEvents([]);
    setStreamingThinking('');
    setStreamingAnswer('');
    setIsThinkingActive(false);
    setIsAnswerActive(false);
  }, []);

  const { isStreaming: isStreamConnecting, startStream, stopStream } = useInterviewStream({
    onEvent: (event) => {
      pushSessionEvent(event);
    },
    onThinkingEvent: handleThinkingEvent,
    onTagEvent: handleTagEvent,
    onStart: (streamSessionId) => {
      if (!sessionId) {
        setSessionId(streamSessionId);
      }
    },
    onComplete: (result) => {
      if (result.sessionId && !sessionId) {
        setSessionId(result.sessionId);
      }
      
      const finalThinking = result.thinking || streamingThinking || undefined;
      const answerMessage: InterviewMessage = {
        id: createMessageId(),
        type: 'answer',
        content: result.answer || result.output || '',
        timestamp: new Date().toISOString(),
        sender: 'model',
        thinking: finalThinking,
        evaluation: {
          userRating: 'pending',
          notes: '',
          criteriaAssessment: {}
        }
      };
      setMessages((prev) => [...prev, answerMessage]);
      setStreamingThinking('');
      setStreamingAnswer('');
      setIsThinkingActive(false);
      setIsAnswerActive(false);
      
      if (result.ok === false) {
        setError(result.error || '模型返回失败');
        setSessionStatus('failed');
        syncTestPanelStatus('failed');
      } else {
        setSessionStatus('success');
        syncTestPanelStatus('success');
        pushSessionEvent({
          type: 'result',
          timestamp: new Date().toISOString(),
          content: '已收到模型响应'
        });
      }
      setResponding(false);
    },
    onError: (error) => {
      setError(error);
      setSessionStatus('failed');
      syncTestPanelStatus('failed');
      setResponding(false);
    }
  });

  const activeRole = roles.find((role) => role.id === selectedRole);
  const activeProvider = providers.find((provider) => provider.id === selectedProvider);

  const templatesByCategory = useMemo(() => {
    const scoped = QUESTION_TEMPLATES.filter(
      (template) => !selectedRole || template.role === selectedRole
    );
    const grouped = new Map<string, QuestionTemplate[]>();
    scoped.forEach((template) => {
      const list = grouped.get(template.category) || [];
      list.push(template);
      grouped.set(template.category, list);
    });
    return Array.from(grouped.entries());
  }, [selectedRole]);

  const answerMessages = useMemo(
    () => messages.filter((message) => message.type === 'answer'),
    [messages]
  );
  const qaPairs = useMemo(() => {
    const pairs: Array<{ question: InterviewMessage | null; answer: InterviewMessage | null }> = [];
    let pendingQuestion: InterviewMessage | null = null;
    messages.forEach((message) => {
      if (message.type === 'question') {
        if (pendingQuestion) {
          pairs.push({ question: pendingQuestion, answer: null });
        }
        pendingQuestion = message;
        return;
      }
      if (message.type === 'answer') {
        if (pendingQuestion) {
          pairs.push({ question: pendingQuestion, answer: message });
          pendingQuestion = null;
          return;
        }
        pairs.push({ question: null, answer: message });
        return;
      }
      pairs.push({ question: null, answer: message });
    });
    if (pendingQuestion) {
      pairs.push({ question: pendingQuestion, answer: null });
    }
    return pairs;
  }, [messages]);
  const hasStreamingContent = streamingThinking || streamingAnswer || isThinkingActive || isAnswerActive;

  useEffect(() => {
    if (hasStreamingContent) {
      conversationEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [streamingThinking, streamingAnswer, hasStreamingContent]);

  const thinkingEnabled = useStreamingMode;
  const showThinkingPanel = thinkingEnabled || thinkingEvents.length > 0;
  const hasPendingEvaluation = answerMessages.some(
    (message) => !message.evaluation || message.evaluation.userRating === 'pending'
  );
  const passedAnswers = answerMessages.filter(
    (message) => message.evaluation?.userRating === 'pass'
  ).length;
  const canFinalize = answerMessages.length > 0 && !hasPendingEvaluation && !responding;
  const compactMode = isDeepTestMode || isFullscreen;
  const showLeftPanel = !isFullscreen;
  const showTemplateColumn = !isDeepTestMode && !isFullscreen;
  const showFloatingTemplatePanel = isDeepTestMode && showTemplatePanel && !isFullscreen;

  useEffect(() => {
    void stopStream();
    setResponding(false);
    setMessages([]);
    setSessionId(null);
    setReport(null);
    setReportSavedPath(null);
    setView('interview');
    setError(null);
    setCustomQuestion('');
    setQuickQuestion('');
    setUserNotes('');
    setSessionStatus('idle');
    onResetTestEvents?.();
    syncTestPanelStatus('idle');
    clearThinkingEvents();
    clearTagEvents();

    setUseStreamingMode(true);
    setShowTemplatePanel(false);
    setIsFullscreen(false);
    setLeftPanelCollapsed(isDeepTestMode && Boolean(selectedRole && selectedProvider));
  }, [clearTagEvents, clearThinkingEvents, isDeepTestMode, onResetTestEvents, selectedRole, selectedProvider, stopStream, syncTestPanelStatus]);

  useEffect(() => {
    return () => {
      void stopStream();
    };
  }, [stopStream]);

  useEffect(() => {
    if (!isDeepTestMode || isFullscreen) return;
    if (selectedRole && selectedProvider) {
      setLeftPanelCollapsed(true);
    }
  }, [isDeepTestMode, isFullscreen, selectedProvider, selectedRole]);

  useEffect(() => {
    if (!isFullscreen) return;
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeydown);
    return () => window.removeEventListener('keydown', handleKeydown);
  }, [isFullscreen]);

  const buildContext = (): Array<{ question: string; answer: string }> => {
    return answerMessages.slice(-3).map((message) => ({
      question: message.question || '',
      answer: message.content
    }));
  };

  const stringifyEventPayload = (payload: unknown, limit = 4000) => {
    try {
      const text = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
      if (text.length <= limit) return text;
      return `${text.slice(0, limit)}...`;
    } catch {
      return String(payload);
    }
  };

  const clearSessionEvents = () => {
    onResetTestEvents?.();
    setSessionStatus('idle');
    syncTestPanelStatus('idle');
  };

  const handleSendQuestion = async (template?: QuestionTemplate, directQuestion?: string) => {
    if (!selectedRole || !selectedProvider) {
      setError('请先选择岗位与模型');
      return;
    }
    const question = (template?.question || directQuestion || customQuestion).trim();
    if (!question) return;

    setError(null);
    setSessionStatus('running');
    syncTestPanelStatus('running');
    pushSessionEvent({
      type: 'command',
      timestamp: new Date().toISOString(),
      content: `POST /llm/interview/ask ${stringifyEventPayload({
        role: selectedRole,
        provider_id: selectedProvider,
        model: selectedModel,
        question
      })}`
    });
    pushSessionEvent({
      type: 'stdout',
      timestamp: new Date().toISOString(),
      content: '发送面试问题...'
    });
    const questionMessage: InterviewMessage = {
      id: createMessageId(),
      type: 'question',
      content: question,
      timestamp: new Date().toISOString(),
      sender: 'user',
      questionId: template?.id,
      expectedCriteria: template?.expectedCriteria
    };
    setMessages((prev) => [...prev, questionMessage]);
    if (!template && !directQuestion) {
      setCustomQuestion('');
    }
    if (directQuestion) {
      setQuickQuestion('');
    }
    if (isDeepTestMode) {
      setShowTemplatePanel(false);
    }

    setResponding(true);
    
    // Use streaming mode if enabled (for real-time output)
    if (useStreamingMode) {
      pushSessionEvent({
        type: 'stdout',
        timestamp: new Date().toISOString(),
        content: 'Using streaming mode for real-time output...'
      });
      
      const streamSessionId = sessionId || `interactive-${createMessageId()}`;
      if (!sessionId) {
        setSessionId(streamSessionId);
      }

      let envOverrides: Record<string, string> | null = null;
      if (resolveEnvOverrides) {
        try {
          envOverrides = await resolveEnvOverrides(selectedProvider);
        } catch {
          envOverrides = null;
        }
      }

      await startStream({
        roleId: selectedRole,
        providerId: selectedProvider,
        model: selectedModel || '',
        question,
        expectedCriteria: template?.expectedCriteria,
        expectsThinking: template ? template.difficulty !== 'basic' : undefined,
        sessionId: streamSessionId,
        context: buildContext(),
        envOverrides: envOverrides || undefined,
      });
      return;
    }
    
    // Standard non-streaming mode
    try {
      const response = await onAskQuestion({
        roleId: selectedRole,
        providerId: selectedProvider,
        model: selectedModel || '',
        question,
        expectedCriteria: template?.expectedCriteria,
        expectsThinking: template ? template.difficulty !== 'basic' : undefined,
        sessionId,
        context: buildContext()
      });
      if (!response) {
        setResponding(false);
        setSessionStatus('failed');
        syncTestPanelStatus('failed');
        pushSessionEvent({
          type: 'error',
          timestamp: new Date().toISOString(),
          content: '未收到模型响应'
        });
        return;
      }
      if (response.sessionId && !sessionId) {
        setSessionId(response.sessionId);
      }
      pushSessionEvent({
        type: 'response',
        timestamp: new Date().toISOString(),
        content: stringifyEventPayload(response)
      });
      // Debug output removed - streaming mode unified
      const answerMessage: InterviewMessage = {
        id: createMessageId(),
        type: 'answer',
        content: response.answer || response.output || '',
        timestamp: new Date().toISOString(),
        sender: 'model',
        questionId: template?.id,
        question,
        expectedCriteria: template?.expectedCriteria,
        thinking: response.thinking,
        evaluation: {
          userRating: 'pending',
          notes: '',
          criteriaAssessment: normalizeCriteriaAssessment(template?.expectedCriteria || [])
        }
      };
      setMessages((prev) => [...prev, answerMessage]);
      if (response.ok === false) {
        setError(response.error || '模型返回失败');
        setSessionStatus('failed');
        syncTestPanelStatus('failed');
        pushSessionEvent({
          type: 'error',
          timestamp: new Date().toISOString(),
          content: response.error || '模型返回失败'
        });
      } else {
        setSessionStatus('success');
        syncTestPanelStatus('success');
        pushSessionEvent({
          type: 'result',
          timestamp: new Date().toISOString(),
          content: '已收到模型响应'
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '发送问题失败');
      setSessionStatus('failed');
      syncTestPanelStatus('failed');
      pushSessionEvent({
        type: 'error',
        timestamp: new Date().toISOString(),
        content: err instanceof Error ? err.message : '发送问题失败'
      });
    } finally {
      setResponding(false);
    }
  };

  const updateEvaluation = (messageId: string, updates: Partial<InterviewMessage['evaluation']>) => {
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== messageId || message.type !== 'answer') {
          return message;
        }
        return {
          ...message,
          evaluation: {
            userRating: 'pending',
            notes: '',
            criteriaAssessment: normalizeCriteriaAssessment(message.expectedCriteria || []),
            ...(message.evaluation || {}),
            ...updates
          }
        };
      })
    );
  };

  const analyzePerformance = (answers: InterviewMessage[]) => {
    const stats = new Map<string, { pass: number; total: number }>();
    answers.forEach((message) => {
      const criteria = message.expectedCriteria || [];
      const assessment = message.evaluation?.criteriaAssessment || {};
      criteria.forEach((item) => {
        const entry = stats.get(item) || { pass: 0, total: 0 };
        entry.total += 1;
        if (assessment[item]) {
          entry.pass += 1;
        }
        stats.set(item, entry);
      });
    });
    const scored = Array.from(stats.entries()).map(([key, value]) => ({
      key,
      rate: value.total ? value.pass / value.total : 0
    }));
    scored.sort((a, b) => b.rate - a.rate);
    const strengths = scored.slice(0, 3).map((item) => item.key);
    const weaknesses = scored.slice(-3).map((item) => item.key).filter(Boolean);
    return { strengths, weaknesses };
  };

  const buildReport = (overallStatus: 'passed' | 'failed'): InteractiveInterviewReport => {
    const startTime = messages[0]?.timestamp || new Date().toISOString();
    const endTime = new Date().toISOString();
    const questions = answerMessages.map((message) => ({
      question: message.question || '',
      answer: message.content,
      evaluation: message.evaluation,
      expectedCriteria: message.expectedCriteria
    }));
    const passedQuestions = answerMessages.filter(
      (message) => message.evaluation?.userRating === 'pass'
    ).length;
    const totalQuestions = answerMessages.length || 1;
    const { strengths, weaknesses } = analyzePerformance(answerMessages);
    const resolvedModel = resolveSelectedModel(
      selectedModel,
      activeProvider?.providerType,
      activeProvider?.model
    );
    return {
      id: sessionId || createMessageId(),
      role: selectedRole || 'pm',
      provider: {
        id: selectedProvider || '',
        name: activeProvider?.name || selectedProvider || '未署名提供商',
        model: resolvedModel.model as string
      },
      startTime,
      endTime,
      overallStatus,
      questions,
      summary: {
        totalQuestions,
        passedQuestions,
        averageRating: passedQuestions / totalQuestions,
        strengths,
        weaknesses,
        recommendation: overallStatus === 'passed' ? '建议通过面试' : '建议进一步提升后重试'
      },
      userNotes
    };
  };

  const finalizeInterview = async (status: 'passed' | 'failed') => {
    if (!selectedRole || !selectedProvider) return;
    setIsFullscreen(false);
    const nextReport = buildReport(status);
    setReport(nextReport);
    setView('report');
    setSaving(true);
    pushSessionEvent({
      type: 'command',
      timestamp: new Date().toISOString(),
      content: `POST /llm/interview/save ${stringifyEventPayload({
        role: selectedRole,
        provider_id: selectedProvider,
        model: selectedModel,
        status
      })}`
    });
    pushSessionEvent({
      type: 'stdout',
      timestamp: new Date().toISOString(),
      content: '保存面试报告...'
    });
    try {
      const result = await onSaveReport({
        roleId: selectedRole,
        providerId: selectedProvider,
        model: selectedModel,
        report: nextReport
      });
      if (result?.report_path) {
        setReportSavedPath(result.report_path);
        pushSessionEvent({
          type: 'result',
          timestamp: new Date().toISOString(),
          content: `报告已保存: ${result.report_path}`
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存面试报告失败');
      pushSessionEvent({
        type: 'error',
        timestamp: new Date().toISOString(),
        content: err instanceof Error ? err.message : '保存面试报告失败'
      });
    } finally {
      setSaving(false);
    }
  };

  const resetInterview = () => {
    const runId = sessionId;
    void stopStream(runId);
    setMessages([]);
    setResponding(false);
    setSessionId(null);
    setReport(null);
    setReportSavedPath(null);
    setView('interview');
    setError(null);
    setCustomQuestion('');
    setQuickQuestion('');
    setUserNotes('');
    setSessionStatus('idle');

    setUseStreamingMode(true);
    onResetTestEvents?.();
    syncTestPanelStatus('idle');
    clearThinkingEvents();
    clearTagEvents();
    setStreamingThinking('');
    setStreamingAnswer('');
    setIsThinkingActive(false);
    setIsAnswerActive(false);
    setShowTemplatePanel(false);
    setIsFullscreen(false);
    setLeftPanelCollapsed(isDeepTestMode && Boolean(selectedRole && selectedProvider));
  };

  if (view === 'report' && report) {
    return (
      <div className="rounded-2xl border border-emerald-500/20 bg-black/30 p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-wide text-text-dim">交互式面试报告</div>
            <h3 className="text-lg font-semibold text-text-main">面试报告 · {activeRole?.label || report.role}</h3>
            <div className="text-[11px] text-text-dim">
              模型：{report.provider.name} • {report.provider.model}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {report.overallStatus === 'passed' ? (
              <span className="px-2 py-1 text-[10px] uppercase font-semibold rounded border bg-emerald-500/20 text-emerald-200 border-emerald-500/30">
                通过
              </span>
            ) : (
              <span className="px-2 py-1 text-[10px] uppercase font-semibold rounded border bg-amber-500/20 text-amber-200 border-amber-500/30">
                失败
              </span>
            )}
            <button
              onClick={resetInterview}
              className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-white/30 flex items-center gap-1"
            >
              <RefreshCw className="size-3" />
              重新开始
            </button>
          </div>
        </div>

        {saving ? (
          <div className="text-[11px] text-text-dim flex items-center gap-2">
            <Loader2 className="size-3 animate-spin" />
            正在保存面试报告...
          </div>
        ) : reportSavedPath ? (
          <div className="text-[11px] text-emerald-300">报告已保存：{reportSavedPath}</div>
        ) : null}
        {error ? (
          <div className="text-[11px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2">
            {error}
          </div>
        ) : null}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div className="rounded-lg border border-white/10 bg-black/20 p-3">
            <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">总问题数</div>
            <div className="text-text-main font-semibold">{report.summary.totalQuestions}</div>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-3">
            <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">通过问题</div>
            <div className="text-text-main font-semibold">{report.summary.passedQuestions}</div>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-3">
            <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">平均评分</div>
            <div className="text-text-main font-semibold">
              {Math.round(report.summary.averageRating * 100)}%
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-white/10 bg-black/20 p-4 text-xs space-y-2">
          <div className="text-[10px] uppercase tracking-wide text-text-dim">总结</div>
          <div className="text-text-main">推荐：{report.summary.recommendation}</div>
          {report.summary.strengths.length > 0 ? (
            <div className="text-text-dim">优势：{report.summary.strengths.join('、')}</div>
          ) : null}
          {report.summary.weaknesses.length > 0 ? (
            <div className="text-text-dim">待提升：{report.summary.weaknesses.join('、')}</div>
          ) : null}
          {report.userNotes ? (
            <div className="text-text-dim">备注：{report.userNotes}</div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`relative ${
        isFullscreen
          ? 'fixed inset-2 z-[70] flex min-h-0 flex-col gap-2 rounded-2xl border border-cyan-400/35 bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.16),_rgba(2,6,23,0.98)_65%)] p-2 shadow-[0_0_40px_rgba(34,211,238,0.25)]'
          : 'flex h-full min-h-0 flex-col gap-5'
      }`}
    >
      {/* Header */}
      <div className={`rounded-2xl border border-cyan-500/25 bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.25),_rgba(3,7,18,0.88)_60%)] shadow-[0_0_30px_rgba(34,211,238,0.18)] ${compactMode ? 'p-2' : 'p-4'}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className={`${compactMode ? 'text-[9px] tracking-[0.16em]' : 'text-[10px] tracking-[0.2em]'} text-cyan-200/80 uppercase`}>交互式面试</div>
            <h3 className={`${compactMode ? 'text-sm' : 'text-lg'} font-semibold text-text-main`}>交互式面试大厅</h3>
            {!compactMode ? (
              <div className="text-[11px] text-text-dim mt-1">
                当前组合：{activeRole?.label || '未选择'} / {activeProvider?.name || '未选择'}
                {selectedModel ? ` • ${selectedModel}` : ''}
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px]">
            <span className={`px-2 py-1 rounded border uppercase tracking-wide ${SESSION_STATUS[sessionStatus].badge}`}>
              {SESSION_STATUS[sessionStatus].label}
            </span>
            <span className={`px-2 py-1 rounded border ${
              isStreamConnecting
                ? 'border-cyan-400/40 bg-cyan-500/20 text-cyan-100'
                : 'border-white/10 bg-black/40 text-text-dim'
            }`}>
              {isStreamConnecting ? '流连接中' : '流连接空闲'}
            </span>
            <label className="flex items-center gap-1.5 rounded border border-white/10 bg-black/40 px-2 py-1 text-text-dim">
              <input
                type="checkbox"
                checked={useStreamingMode}
                onChange={(event) => setUseStreamingMode(event.target.checked)}
                className="h-3 w-3 rounded border-white/20 bg-black/40"
              />
              实时流式解析
            </label>
            <button
              type="button"
              onClick={clearSessionEvents}
              className="px-2 py-1 rounded border border-white/10 hover:border-cyan-400/50 text-text-dim hover:text-cyan-100"
            >
              清空公共日志
            </button>
            {isDeepTestMode ? (
              <>
                {!isFullscreen ? (
                  <button
                    type="button"
                    onClick={() => setShowTemplatePanel((prev) => !prev)}
                    className={`px-2 py-1 rounded border transition-colors ${
                      showTemplatePanel
                        ? 'border-fuchsia-400/50 bg-fuchsia-500/20 text-fuchsia-100'
                        : 'border-white/10 text-text-dim hover:border-fuchsia-400/50 hover:text-fuchsia-100'
                    }`}
                  >
                    {showTemplatePanel ? '隐藏模板' : '显示模板'}
                  </button>
                ) : null}
                {!isFullscreen ? (
                  <button
                    type="button"
                    onClick={() => setLeftPanelCollapsed((prev) => !prev)}
                    className="px-2 py-1 rounded border border-white/10 text-text-dim hover:border-cyan-400/50 hover:text-cyan-100"
                  >
                    {leftPanelCollapsed ? '展开侧栏' : '收起侧栏'}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => setIsFullscreen((prev) => !prev)}
                  className="px-2 py-1 rounded border border-cyan-400/40 bg-cyan-500/15 text-cyan-100 hover:bg-cyan-500/25 inline-flex items-center gap-1"
                  title={isFullscreen ? '退出全屏（Esc）' : '进入全屏'}
                >
                  {isFullscreen ? <Minimize2 className="size-3" /> : <Maximize2 className="size-3" />}
                  {isFullscreen ? '退出全屏' : '全屏'}
                </button>
              </>
            ) : null}
          </div>
        </div>
        {!compactMode ? (
          <div className="text-[10px] text-text-dim mt-3">
          面试日志已迁移到公共浮动 TestPanel（可悬浮/拖拽），本页面仅保留面试交互与评估。
          </div>
        ) : null}
      </div>

      {/* Main content area - 响应式布局 */}
      <div
        className={`grid flex-1 min-h-0 overflow-hidden ${
          isFullscreen
            ? 'grid-cols-1 gap-2'
            : isDeepTestMode
              ? 'grid-cols-1 xl:grid-cols-[minmax(160px,0.72fr)_minmax(0,2.28fr)] gap-3'
              : 'grid-cols-1 xl:grid-cols-[1.05fr_1.95fr_1.15fr] gap-5'
        }`}
      >
        {/* Left Panel - Role Selection */}
        {showLeftPanel ? (
          <div className={`${compactMode ? 'grid grid-rows-[auto_1fr] gap-2' : 'grid grid-rows-[auto_1fr_auto_1fr] gap-4'} min-h-0 overflow-hidden`}>
            <div className="flex items-center justify-between">
              <div className="text-xs font-semibold text-text-main uppercase tracking-wide">面试岗位</div>
              {isDeepTestMode ? (
                <button
                  type="button"
                  onClick={() => setLeftPanelCollapsed((prev) => !prev)}
                  className="px-2 py-1 text-[10px] rounded border border-white/10 text-text-dim hover:border-cyan-400/50"
                >
                  {leftPanelCollapsed ? '展开' : '收起'}
                </button>
              ) : null}
            </div>

            {leftPanelCollapsed && isDeepTestMode ? (
              <div className="space-y-2">
                <div className="rounded-xl border border-cyan-500/25 bg-cyan-500/[0.08] p-2 text-[10px]">
                  <div className="text-text-dim">岗位</div>
                  <div className="text-text-main mt-1">{activeRole?.label || '未选择'}</div>
                </div>
                <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.08] p-2 text-[10px]">
                  <div className="text-text-dim">提供商</div>
                  <div className="text-text-main mt-1">{activeProvider?.name || '未选择'}</div>
                  <div className="text-text-dim mt-1">{selectedModel || activeProvider?.model || '未设置模型'}</div>
                </div>
              </div>
            ) : (
              <>
                <div className="space-y-2 min-h-0 overflow-auto pr-1">
                  {roles.map((role) => {
                    const isActive = role.id === selectedRole;
                    const badge = ROLE_BADGES[role.id] || 'bg-white/10 text-text-main border-white/20';
                    return (
                      <button
                        key={role.id}
                        onClick={() => onSelectRole(role.id)}
                        className={`w-full text-left rounded-xl border ${compactMode ? 'p-2.5' : 'p-4'} transition-all ${
                          isActive
                            ? 'border-cyan-400/60 bg-cyan-500/10'
                            : 'border-white/10 bg-white/5 hover:border-white/20'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`}>
                              {role.label}
                            </span>
                            {role.readiness?.ready ? (
                              <CheckCircle2 className="size-4 text-emerald-400" />
                            ) : (
                              <AlertTriangle className="size-4 text-amber-300" />
                            )}
                          </div>
                          {!compactMode ? (
                            <div className="text-[10px] text-text-dim uppercase tracking-wide">
                              {role.requiresThinking ? '需要思考' : '可选思考'}
                            </div>
                          ) : null}
                        </div>
                        {!compactMode ? (
                          <div className="mt-2 text-xs text-text-dim">{role.description}</div>
                        ) : null}
                      </button>
                    );
                  })}
                </div>

                <div className="text-xs font-semibold text-text-main uppercase tracking-wide">模型选择</div>
                <div className="space-y-2 min-h-0 overflow-auto pr-1">
                  {providers.map((provider) => {
                    const isActive = provider.id === selectedProvider;
                    const styles = STATUS_STYLES[provider.status] || STATUS_STYLES.untested;
                    return (
                      <button
                        key={provider.id}
                        onClick={() => onSelectProvider(provider.id)}
                        className={`w-full text-left rounded-xl border ${compactMode ? 'p-2.5' : 'p-3'} transition-all ${
                          isActive
                            ? 'border-emerald-400/50 bg-emerald-500/10'
                            : `${styles.border} ${styles.bg} hover:border-white/20`
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 min-w-0">
                            <div className="text-xs text-text-main font-semibold truncate">{provider.name}</div>
                            {provider.interviewStatus && provider.interviewStatus !== 'none' ? (
                              <span className={`text-[9px] uppercase px-1.5 py-0.5 rounded border ${
                                provider.interviewStatus === 'passed'
                                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                                  : 'border-rose-500/40 bg-rose-500/10 text-rose-300'
                              }`}>
                                {provider.interviewStatus === 'passed' ? '面试通过' : '面试失败'}
                              </span>
                            ) : null}
                          </div>
                          <div className="flex items-center gap-2">
                            {!compactMode ? (
                              <span className="text-[10px] text-text-dim">{provider.model || '未设置模型'}</span>
                            ) : null}
                            <span className={`text-[9px] uppercase px-2 py-0.5 rounded border ${styles.border} ${styles.text}`}>
                              {STATUS_LABELS[provider.status]}
                            </span>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        ) : null}

        {/* Second Panel - Question Templates */}
        {showTemplateColumn ? (
          <div className="flex flex-col gap-4 min-h-0 overflow-hidden xl:order-3">
            <div className="text-xs font-semibold text-text-main uppercase tracking-wide">问题模板库</div>
            <div className="space-y-3 flex-1 min-h-0 overflow-auto pr-1">
              {templatesByCategory.length === 0 ? (
                <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-xs text-text-dim">
                  请选择岗位以显示对应问题模板。
                </div>
              ) : (
                templatesByCategory.map(([category, templates]) => (
                  <div key={category} className="rounded-xl border border-fuchsia-500/20 bg-fuchsia-500/[0.05] p-3 space-y-2">
                    <div className="text-[11px] font-semibold text-text-main">{category}</div>
                    <div className="space-y-2">
                      {templates.map((template) => (
                        <button
                          key={template.id}
                          onClick={() => handleSendQuestion(template)}
                          disabled={responding}
                          className="w-full text-left text-[11px] px-3 py-2 rounded border border-white/10 bg-black/40 hover:border-cyan-400/40 disabled:opacity-60"
                        >
                          <div className="text-text-main font-semibold">{template.title}</div>
                          <div className="text-text-dim mt-1">{template.question}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] p-4 space-y-3">
              <div className="text-xs font-semibold text-text-main uppercase tracking-wide">自定义问题</div>
              <textarea
                value={customQuestion}
                onChange={(event) => setCustomQuestion(event.target.value)}
                placeholder="输入自定义面试问题..."
                rows={3}
                className="w-full rounded-lg border border-white/10 bg-black/40 p-2 text-xs text-text-main"
              />
              <button
                onClick={() => handleSendQuestion()}
                disabled={responding || !customQuestion.trim()}
                className="w-full px-3 py-2 text-[11px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1"
              >
                <Send className="size-3" />
                {responding ? '发送中...' : '发送问题'}
              </button>
            </div>
          </div>
        ) : null}

        {/* Center Panel - Real-time Conversation */}
        <div className={`flex min-h-0 flex-col overflow-hidden xl:order-2 ${compactMode ? 'gap-2' : 'gap-4'}`}>
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide flex-shrink-0">实时对话区</div>
          <div className={`rounded-2xl border border-cyan-500/20 bg-[linear-gradient(165deg,rgba(8,16,38,0.92),rgba(4,8,22,0.92))] flex-1 min-h-0 flex flex-col overflow-hidden shadow-[0_0_24px_rgba(34,211,238,0.12)] ${compactMode ? 'p-2 space-y-2' : 'p-4 space-y-3'}`}>
            <div className="text-[11px] text-text-dim flex-shrink-0">
              对话时间线（支持长文本滚动）
            </div>

            {showThinkingPanel ? (
              <div className={`flex-shrink-0 space-y-2 overflow-hidden ${compactMode ? 'max-h-36' : 'max-h-52'}`}>
                <div className={`${compactMode ? 'max-h-32' : 'max-h-24'} overflow-y-auto`}>
                  <RealtimeThinkingDisplay
                    events={thinkingEvents}
                    enabled={thinkingEnabled}
                    isStreaming={responding && thinkingEnabled}
                    onClear={clearThinkingEvents}
                  />
                </div>
                <div className={`${compactMode ? 'max-h-32' : 'max-h-24'} overflow-y-auto`}>
                  <StreamingTags
                    events={tagEvents}
                    isStreaming={responding}
                    onClear={clearTagEvents}
                  />
                </div>
              </div>
            ) : null}

            {messages.length === 0 ? (
              <div className="text-xs text-text-dim flex-1 flex items-center justify-center min-h-0">暂无对话记录，请先选择模板问题或输入自定义问题。</div>
            ) : (
              <div className={`flex-1 min-h-0 overflow-y-auto ${compactMode ? 'space-y-2 pr-1' : 'space-y-3 pr-2'}`}>
                {qaPairs.map((pair, index) => {
                  const question = pair.question;
                  const answer = pair.answer;
                  const criteria = answer?.expectedCriteria || question?.expectedCriteria || [];
                  return (
                  <div
                    key={question?.id || answer?.id || `qa-${index}`}
                    className={`rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] text-xs flex-shrink-0 ${compactMode ? 'p-2.5 space-y-2' : 'p-4 space-y-3'}`}
                  >
                    <div className="text-[10px] uppercase tracking-wide text-cyan-100/80">
                      问答 {index + 1}
                    </div>

                    {question ? (
                      <div className={`rounded-lg border border-cyan-400/30 bg-cyan-500/10 ${compactMode ? 'p-2' : 'p-3'}`}>
                        <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">提问</div>
                        <div className="text-text-main whitespace-pre-wrap break-words">{question.content}</div>
                      </div>
                    ) : null}

                    {answer ? (
                      <div className={`rounded-lg border border-emerald-500/25 bg-emerald-500/10 ${compactMode ? 'p-2 space-y-1.5' : 'p-3 space-y-2'}`}>
                        <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">作答</div>
                        <div className="text-text-main whitespace-pre-wrap break-words">{answer.content}</div>
                        {answer.thinking ? (
                          <div className="text-[11px] text-text-dim whitespace-pre-wrap break-words">
                            <span className="text-[10px] uppercase tracking-wide">思考链</span>
                            <div>{answer.thinking}</div>
                          </div>
                        ) : null}

                        <div className="space-y-2 pt-2 border-t border-white/10">
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              onClick={() => updateEvaluation(answer.id, { userRating: 'pass' })}
                              className={`px-2 py-1 text-[10px] rounded border flex items-center gap-1 ${
                                answer.evaluation?.userRating === 'pass'
                                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                                  : 'border-white/10 text-text-dim hover:border-emerald-500/30'
                              }`}
                            >
                              <Check className="size-3" />
                              通过
                            </button>
                            <button
                              onClick={() => updateEvaluation(answer.id, { userRating: 'fail' })}
                              className={`px-2 py-1 text-[10px] rounded border flex items-center gap-1 ${
                                answer.evaluation?.userRating === 'fail'
                                  ? 'border-rose-500/40 bg-rose-500/10 text-rose-200'
                                  : 'border-white/10 text-text-dim hover:border-rose-500/30'
                              }`}
                            >
                              <XCircle className="size-3" />
                              失败
                            </button>
                          </div>

                          {criteria.length > 0 ? (
                            <div className="space-y-1 text-[10px] text-text-dim">
                              <div className="uppercase tracking-wide text-[10px]">评估指标</div>
                              <div className="flex flex-wrap gap-x-3 gap-y-1">
                                {criteria.map((item) => (
                                  <label key={item} className="flex items-center gap-1 cursor-pointer hover:text-text-main">
                                    <input
                                      type="checkbox"
                                      checked={Boolean(answer.evaluation?.criteriaAssessment?.[item])}
                                      onChange={(event) => {
                                        updateEvaluation(answer.id, {
                                          criteriaAssessment: {
                                            ...(answer.evaluation?.criteriaAssessment || {}),
                                            [item]: event.target.checked
                                          }
                                        });
                                      }}
                                      className="h-3 w-3 rounded border-white/20 bg-black/40"
                                    />
                                    <span className="break-words">{item}</span>
                                  </label>
                                ))}
                              </div>
                            </div>
                          ) : null}

                          <input
                            value={answer.evaluation?.notes || ''}
                            onChange={(event) => updateEvaluation(answer.id, { notes: event.target.value })}
                            placeholder="备注（可选）"
                            className="w-full rounded border border-white/10 bg-black/40 px-2 py-1 text-[10px] text-text-main"
                          />
                        </div>
                      </div>
                    ) : (
                      <div className="text-[11px] text-text-dim">
                        {responding && !hasStreamingContent ? '等待回答中...' : !responding ? '暂无回答' : null}
                      </div>
                    )}

                    {/* Inline streaming thinking/answer bubbles for the pending question */}
                    {!answer && responding && hasStreamingContent && index === qaPairs.length - 1 ? (
                      <div className="space-y-2">
                        {(streamingThinking || isThinkingActive) ? (
                          <div className={`rounded-lg border border-amber-400/40 bg-amber-500/10 ${compactMode ? 'p-2' : 'p-3'}`}>
                            <div className="flex items-center gap-2 mb-1.5">
                              <Brain className="size-3 text-amber-300" />
                              <span className="text-[10px] uppercase tracking-wide text-amber-300 font-semibold">Architect thinking aloud</span>
                              {isThinkingActive ? (
                                <Loader2 className="size-3 text-amber-300 animate-spin" />
                              ) : null}
                            </div>
                            <div className="text-text-main text-xs whitespace-pre-wrap break-words leading-relaxed">
                              {streamingThinking}
                              {isThinkingActive ? (
                                <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-amber-400 align-middle" />
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                        {(streamingAnswer || isAnswerActive) ? (
                          <div className={`rounded-lg border border-emerald-500/25 bg-emerald-500/10 ${compactMode ? 'p-2' : 'p-3'}`}>
                            <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">作答中</div>
                            <div className="text-text-main text-xs whitespace-pre-wrap break-words leading-relaxed">
                              {streamingAnswer}
                              {isAnswerActive ? (
                                <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-emerald-400 align-middle" />
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                );
                })}
                <div ref={conversationEndRef} />
              </div>
            )}

            {error ? (
              <div className="text-[10px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2 flex-shrink-0">
                {error}
              </div>
            ) : null}

            <div className={`rounded-lg border border-white/10 bg-black/60 flex-shrink-0 ${compactMode ? 'p-2 space-y-1.5' : 'p-3 space-y-2'}`}>
              <div className="text-[10px] uppercase tracking-wide text-text-dim">继续追问</div>
              <textarea
                value={quickQuestion}
                onChange={(event) => setQuickQuestion(event.target.value)}
                placeholder="在这里输入追问问题..."
                rows={compactMode ? 1 : 2}
                className="w-full rounded border border-white/10 bg-black/40 px-2 py-1 text-[10px] text-text-main resize-none"
              />
              <button
                onClick={() => handleSendQuestion(undefined, quickQuestion)}
                disabled={responding || !quickQuestion.trim()}
                className={`w-full px-3 text-[10px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1 ${compactMode ? 'py-1' : 'py-1.5'}`}
              >
                <Send className="size-3" />
                {responding ? '发送中...' : '发送追问'}
              </button>
            </div>
          </div>

          {!isFullscreen ? (
            <div className={`rounded-xl border border-white/10 bg-black/30 flex-shrink-0 ${compactMode ? 'p-2.5 space-y-2' : 'p-4 space-y-3'}`}>
              <div className="text-xs font-semibold text-text-main uppercase tracking-wide">面试控制</div>
              <textarea
                value={userNotes}
                onChange={(event) => setUserNotes(event.target.value)}
                placeholder="面试官备注（可选）"
                rows={compactMode ? 1 : 2}
                className="w-full rounded-lg border border-white/10 bg-black/40 p-2 text-xs text-text-main resize-none"
              />
              <div className={`flex ${compactMode ? 'flex-row flex-wrap' : 'flex-col'} gap-2`}>
                <button
                  onClick={() => finalizeInterview('passed')}
                  disabled={!canFinalize || passedAnswers === 0}
                  className={`px-3 ${compactMode ? 'py-1.5 text-[10px]' : 'py-2 text-[11px]'} font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1`}
                >
                  <CheckCircle2 className="size-3" />
                  通过
                </button>
                <button
                  onClick={() => finalizeInterview('failed')}
                  disabled={answerMessages.length === 0 || responding}
                  className={`px-3 ${compactMode ? 'py-1.5 text-[10px]' : 'py-2 text-[11px]'} font-semibold bg-rose-500/80 hover:bg-rose-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1`}
                >
                  <XCircle className="size-3" />
                  失败
                </button>
                <button
                  onClick={resetInterview}
                  className={`px-3 ${compactMode ? 'py-1.5 text-[10px]' : 'py-2 text-[11px]'} border border-white/10 rounded hover:border-white/30 flex items-center justify-center gap-1`}
                >
                  <RefreshCw className="size-3" />
                  重置会话
                </button>
              </div>
            </div>
          ) : null}
        </div>

      </div>

      {showFloatingTemplatePanel ? (
        <div className="absolute right-2 top-14 z-40 w-[min(420px,94vw)] rounded-xl border border-fuchsia-400/35 bg-[linear-gradient(160deg,rgba(46,16,66,0.86),rgba(7,10,22,0.96))] p-3 shadow-[0_0_24px_rgba(217,70,239,0.24)]">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] uppercase tracking-wide text-fuchsia-100">模板问题面板</div>
            <button
              type="button"
              onClick={() => setShowTemplatePanel(false)}
              className="px-2 py-1 rounded border border-white/10 text-[10px] text-text-dim hover:border-fuchsia-400/50 hover:text-fuchsia-100"
            >
              关闭
            </button>
          </div>
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {templatesByCategory.length === 0 ? (
              <div className="rounded border border-white/10 bg-white/5 p-2 text-[10px] text-text-dim">
                请选择岗位以显示对应问题模板。
              </div>
            ) : (
              templatesByCategory.map(([category, templates]) => (
                <div key={category} className="rounded border border-white/10 bg-black/35 p-2 space-y-2">
                  <div className="text-[10px] font-semibold text-text-main">{category}</div>
                  {templates.map((template) => (
                    <button
                      key={template.id}
                      onClick={() => handleSendQuestion(template)}
                      disabled={responding}
                      className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-white/10 bg-black/35 hover:border-cyan-400/40 disabled:opacity-60"
                    >
                      <div className="text-text-main">{template.title}</div>
                      <div className="text-text-dim mt-1">{template.question}</div>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
          <div className="mt-2 rounded border border-emerald-500/25 bg-emerald-500/[0.08] p-2 space-y-2">
            <div className="text-[10px] uppercase tracking-wide text-emerald-100">自定义问题</div>
            <textarea
              value={customQuestion}
              onChange={(event) => setCustomQuestion(event.target.value)}
              placeholder="输入自定义问题..."
              rows={2}
              className="w-full rounded border border-white/10 bg-black/40 p-2 text-[10px] text-text-main resize-none"
            />
            <button
              onClick={() => handleSendQuestion()}
              disabled={responding || !customQuestion.trim()}
              className="w-full px-3 py-1.5 text-[10px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1"
            >
              <Send className="size-3" />
              {responding ? '发送中...' : '发送问题'}
            </button>
          </div>
        </div>
      ) : null}

      {isFullscreen ? (
        <div className="absolute right-2 bottom-2 z-50 rounded-xl border border-cyan-400/35 bg-black/70 backdrop-blur-xl p-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setIsFullscreen(false)}
            className="px-2 py-1 text-[10px] rounded border border-cyan-400/40 text-cyan-100 hover:bg-cyan-500/20 inline-flex items-center gap-1"
            title="退出全屏（Esc）"
          >
            <Minimize2 className="size-3" />
            退出全屏
          </button>
          <button
            type="button"
            onClick={() => finalizeInterview('passed')}
            disabled={!canFinalize || passedAnswers === 0}
            className="px-2 py-1 text-[10px] rounded border border-emerald-500/40 text-emerald-100 bg-emerald-500/20 disabled:opacity-60"
          >
            通过
          </button>
          <button
            type="button"
            onClick={() => finalizeInterview('failed')}
            disabled={answerMessages.length === 0 || responding}
            className="px-2 py-1 text-[10px] rounded border border-rose-500/40 text-rose-100 bg-rose-500/20 disabled:opacity-60"
          >
            失败
          </button>
          <button
            type="button"
            onClick={resetInterview}
            className="px-2 py-1 text-[10px] rounded border border-white/10 text-text-dim hover:border-white/30"
          >
            重置
          </button>
        </div>
      ) : null}
    </div>
  );
}
