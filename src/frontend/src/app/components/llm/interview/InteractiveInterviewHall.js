import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2, Send, RefreshCw, Check, XCircle, Maximize2, Minimize2, Brain, } from "lucide-react";
import { RealtimeThinkingDisplay } from "./RealtimeThinkingDisplay";
import { StreamingTags } from "./StreamingTags";
import { useInterviewStream, } from "./useInterviewStream";
import { getLlmRoleDefinition } from "../roleDefinitions";
const MODEL_FALLBACKS = {
    openai: "gpt-4",
    openai_compat: "gpt-4",
    anthropic: "claude-3-sonnet-20240229",
    anthropic_compat: "claude-3-sonnet-20240229",
    kimi: "kimi-k2-thinking-turbo",
    minimax: "abab6.5-chat",
    gemini_api: "gemini-1.5-pro",
    ollama: "llama2",
    codex_cli: "gpt-4-codex",
    codex_sdk: "gpt-4",
    gemini_cli: "gemini-1.5-pro",
    custom_https: "gpt-4",
};
function resolveSelectedModel(selectedModel, providerType, activeProviderModel) {
    if (selectedModel && selectedModel.trim()) {
        return {
            model: selectedModel.trim(),
            source: "role_config",
            isValid: true,
        };
    }
    if (activeProviderModel && activeProviderModel.trim()) {
        return {
            model: activeProviderModel.trim(),
            source: "provider_config",
            isValid: true,
        };
    }
    if (providerType) {
        const fallbackModel = MODEL_FALLBACKS[providerType];
        if (fallbackModel) {
            return {
                model: fallbackModel,
                source: "hardcoded_fallback",
                isValid: true,
                warning: `使用默认模型 ${fallbackModel}`,
            };
        }
    }
    return {
        model: "gpt-4",
        source: "hardcoded_fallback",
        isValid: false,
        warning: "无法确定模型，改用通用兜底模型",
    };
}
const STATUS_STYLES = {
    ready: {
        border: "border-emerald-500/30",
        bg: "bg-emerald-500/[0.08]",
        dot: "bg-emerald-400",
        text: "text-emerald-300",
    },
    failed: {
        border: "border-rose-500/30",
        bg: "bg-rose-500/[0.08]",
        dot: "bg-rose-400",
        text: "text-rose-300",
    },
    testing: {
        border: "border-amber-500/30",
        bg: "bg-amber-500/[0.08]",
        dot: "bg-amber-300",
        text: "text-amber-200",
    },
    untested: {
        border: "border-white/[0.08]",
        bg: "bg-white/[0.04]",
        dot: "bg-white/30",
        text: "text-text-dim",
    },
};
const STATUS_LABELS = {
    ready: "连通正常",
    failed: "连通失败",
    testing: "连通测试中",
    untested: "连通未测",
};
const SESSION_STATUS = {
    idle: {
        label: "待命",
        badge: "bg-white/[0.06] text-text-dim border-white/10",
    },
    running: {
        label: "进行中",
        badge: "bg-amber-500/[0.12] text-amber-200 border-amber-500/25",
    },
    success: {
        label: "完成",
        badge: "bg-emerald-500/[0.12] text-emerald-200 border-emerald-500/25",
    },
    failed: {
        label: "失败",
        badge: "bg-rose-500/[0.12] text-rose-200 border-rose-500/25",
    },
};
const QUESTION_TEMPLATES = [
    {
        id: "pm-project-analysis",
        category: "项目规划类",
        title: "项目需求分析",
        question: "请分析这个项目需求并制定实施计划，包括时间安排、资源分配和风险评估。",
        expectedCriteria: ["分析深度", "计划完整性", "风险评估"],
        difficulty: "intermediate",
        role: "pm",
    },
    {
        id: "pm-conflict-resolution",
        category: "冲突协调类",
        title: "技术分歧协调",
        question: "开发团队在前端技术选型上出现分歧，作为PM你如何协调解决？请说明具体步骤和考虑因素。",
        expectedCriteria: ["思考过程", "解决方案", "沟通策略"],
        difficulty: "advanced",
        role: "pm",
    },
    {
        id: "director-architecture",
        category: "架构决策类",
        title: "架构方案选择",
        question: "如果需要在稳定性和交付速度之间权衡，你会如何做架构决策？请给出判断依据。",
        expectedCriteria: ["技术分析", "权衡取舍", "风险评估"],
        difficulty: "advanced",
        role: "director",
    },
    {
        id: "director-code-review",
        category: "代码审查类",
        title: "代码质量改进",
        question: "请说明你在代码审查中如何发现高风险问题，并提出改进建议。",
        expectedCriteria: ["问题识别", "改进方案", "质量标准"],
        difficulty: "intermediate",
        role: "director",
    },
    {
        id: "qa-test-strategy",
        category: "测试策略类",
        title: "测试计划制定",
        question: "面对一个迭代频繁的项目，你会如何制定测试策略以确保质量？",
        expectedCriteria: ["测试覆盖", "风险识别", "执行策略"],
        difficulty: "intermediate",
        role: "qa",
    },
    {
        id: "qa-defect-analysis",
        category: "缺陷分析类",
        title: "线上故障复盘",
        question: "线上出现严重缺陷时，你会如何定位原因并推动修复？",
        expectedCriteria: ["问题定位", "根因分析", "协作推进"],
        difficulty: "advanced",
        role: "qa",
    },
    {
        id: "architect-guide",
        category: "文档编写类",
        title: "功能说明文档",
        question: "请为一个新功能编写简明的使用说明，包含前置条件与操作步骤。",
        expectedCriteria: ["文档完整性", "表达清晰度", "可操作性"],
        difficulty: "basic",
        role: "architect",
    },
    {
        id: "architect-onboarding",
        category: "用户引导类",
        title: "快速上手指南",
        question: "你会如何设计一个新用户的快速上手指南？请说明结构与重点。",
        expectedCriteria: ["结构设计", "用户视角", "示例准确性"],
        difficulty: "intermediate",
        role: "architect",
    },
    {
        id: "resident-agi-decision-boundary",
        category: "自治决策类",
        title: "无人值守决策边界",
        question: "当平台无人值守运行时，你如何判断一个修复建议是否只能作为 advisory，而不能直接转成写入决策？请给出证据链和阻断条件。",
        expectedCriteria: ["权限边界", "证据链", "风险阻断"],
        difficulty: "advanced",
        role: "resident_agi",
    },
    {
        id: "resident-agi-audit-handoff",
        category: "审计交接类",
        title: "AGI 审计交接",
        question: "如果 Director 进入修复阶段且确定性策略覆盖不足，你会如何调用平台能力完成审计、建议和交接，同时避免越权执行？",
        expectedCriteria: ["能力选择", "交接协议", "越权防护"],
        difficulty: "advanced",
        role: "resident_agi",
    },
];
const createMessageId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const normalizeCriteriaAssessment = (criteria, current) => {
    const next = { ...(current || {}) };
    criteria.forEach((item) => {
        if (typeof next[item] !== "boolean") {
            next[item] = false;
        }
    });
    return next;
};
export function InteractiveInterviewHall({ roles, providers, selectedRole, selectedProvider, selectedModel, onSelectRole, onSelectProvider, onAskQuestion, onSaveReport, resolveEnvOverrides, onTestEvent, onResetTestEvents, onSyncTestPanelState, isDeepTestMode = false, }) {
    const [messages, setMessages] = useState([]);
    const [customQuestion, setCustomQuestion] = useState("");
    const [quickQuestion, setQuickQuestion] = useState("");
    const [responding, setResponding] = useState(false);
    const [sessionId, setSessionId] = useState(null);
    const [view, setView] = useState("interview");
    const [report, setReport] = useState(null);
    const [reportSavedPath, setReportSavedPath] = useState(null);
    const [userNotes, setUserNotes] = useState("");
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);
    const [sessionStatus, setSessionStatus] = useState("idle");
    const [thinkingEvents, setThinkingEvents] = useState([]);
    const [tagEvents, setTagEvents] = useState([]);
    const [useStreamingMode, setUseStreamingMode] = useState(true); // Enable streaming by default
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
    const [showTemplatePanel, setShowTemplatePanel] = useState(false);
    const [streamingThinking, setStreamingThinking] = useState("");
    const [streamingAnswer, setStreamingAnswer] = useState("");
    const [isThinkingActive, setIsThinkingActive] = useState(false);
    const [isAnswerActive, setIsAnswerActive] = useState(false);
    const conversationEndRef = useRef(null);
    const pushSessionEvent = useCallback((event) => {
        onTestEvent?.(event);
    }, [onTestEvent]);
    const syncTestPanelStatus = useCallback((status) => {
        if (!selectedRole || !selectedProvider)
            return;
        onSyncTestPanelState?.({
            providerId: selectedProvider,
            roleId: selectedRole,
            model: selectedModel,
            status,
        });
    }, [onSyncTestPanelState, selectedModel, selectedProvider, selectedRole]);
    const handleThinkingEvent = useCallback((event) => {
        setThinkingEvents((prev) => {
            const next = [...prev];
            const existingIndex = next.findIndex((item) => item.id === event.id && item.kind === event.kind);
            if (existingIndex >= 0) {
                next[existingIndex] = { ...next[existingIndex], ...event };
                return next;
            }
            next.push(event);
            const maxEvents = 200;
            if (next.length <= maxEvents)
                return next;
            return next.slice(next.length - maxEvents);
        });
        if (event.kind === "reasoning" && event.text) {
            setStreamingThinking((prev) => (prev ? prev + "\n" : "") + event.text);
            setIsThinkingActive(true);
        }
    }, []);
    const clearThinkingEvents = useCallback(() => setThinkingEvents([]), []);
    const handleTagEvent = useCallback((event) => {
        setTagEvents((prev) => {
            const next = [...prev, event];
            const maxEvents = 500;
            if (next.length <= maxEvents)
                return next;
            return next.slice(next.length - maxEvents);
        });
        switch (event.type) {
            case "thinking_start":
                setIsThinkingActive(true);
                setStreamingThinking("");
                break;
            case "thinking_chunk":
                if (event.data.content) {
                    setStreamingThinking((prev) => prev + event.data.content);
                }
                break;
            case "thinking_end":
                setIsThinkingActive(false);
                break;
            case "answer_start":
                setIsAnswerActive(true);
                setStreamingAnswer("");
                break;
            case "answer_chunk":
                if (event.data.content) {
                    setStreamingAnswer((prev) => prev + event.data.content);
                }
                break;
            case "answer_end":
                setIsAnswerActive(false);
                break;
        }
    }, []);
    const clearTagEvents = useCallback(() => {
        setTagEvents([]);
        setStreamingThinking("");
        setStreamingAnswer("");
        setIsThinkingActive(false);
        setIsAnswerActive(false);
    }, []);
    const { isStreaming: isStreamConnecting, startStream, stopStream, } = useInterviewStream({
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
            const answerMessage = {
                id: createMessageId(),
                type: "answer",
                content: result.answer || result.output || "",
                timestamp: new Date().toISOString(),
                sender: "model",
                thinking: finalThinking,
                evaluation: {
                    userRating: "pending",
                    notes: "",
                    criteriaAssessment: {},
                },
            };
            setMessages((prev) => [...prev, answerMessage]);
            if (result.ok === false) {
                setError(result.error || "模型返回失败");
                setSessionStatus("failed");
                syncTestPanelStatus("failed");
            }
            else {
                setSessionStatus("success");
                syncTestPanelStatus("success");
                pushSessionEvent({
                    type: "result",
                    timestamp: new Date().toISOString(),
                    content: "已收到模型响应",
                });
            }
            setIsThinkingActive(false);
            setIsAnswerActive(false);
            setResponding(false);
        },
        onError: (error) => {
            setError(error);
            setSessionStatus("failed");
            syncTestPanelStatus("failed");
            setResponding(false);
        },
    });
    const activeRole = roles.find((role) => role.id === selectedRole);
    const activeProvider = providers.find((provider) => provider.id === selectedProvider);
    const templatesByCategory = useMemo(() => {
        const scoped = QUESTION_TEMPLATES.filter((template) => !selectedRole || template.role === selectedRole);
        const grouped = new Map();
        scoped.forEach((template) => {
            const list = grouped.get(template.category) || [];
            list.push(template);
            grouped.set(template.category, list);
        });
        return Array.from(grouped.entries());
    }, [selectedRole]);
    const answerMessages = useMemo(() => messages.filter((message) => message.type === "answer"), [messages]);
    const qaPairs = useMemo(() => {
        const pairs = [];
        let pendingQuestion = null;
        messages.forEach((message) => {
            if (message.type === "question") {
                if (pendingQuestion) {
                    pairs.push({ question: pendingQuestion, answer: null });
                }
                pendingQuestion = message;
                return;
            }
            if (message.type === "answer") {
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
    const displayedThinkingEvents = useMemo(() => {
        const next = [...thinkingEvents];
        if (streamingThinking || isThinkingActive) {
            next.push({
                id: "live-stream-thinking",
                kind: "reasoning",
                timestamp: new Date().toISOString(),
                status: isThinkingActive ? "in_progress" : "completed",
                thinking: streamingThinking,
            });
            return next;
        }
        if (streamingAnswer || isAnswerActive) {
            next.push({
                id: "live-stream-answer",
                kind: "agent_message",
                timestamp: new Date().toISOString(),
                status: isAnswerActive ? "in_progress" : "completed",
                answer: streamingAnswer,
            });
        }
        return next;
    }, [
        isAnswerActive,
        isThinkingActive,
        streamingAnswer,
        streamingThinking,
        thinkingEvents,
    ]);
    useEffect(() => {
        if (hasStreamingContent) {
            conversationEndRef.current?.scrollIntoView({
                behavior: "smooth",
                block: "end",
            });
        }
    }, [streamingThinking, streamingAnswer, hasStreamingContent]);
    const thinkingEnabled = useStreamingMode;
    const showThinkingPanel = thinkingEnabled || thinkingEvents.length > 0;
    const hasPendingEvaluation = answerMessages.some((message) => !message.evaluation || message.evaluation.userRating === "pending");
    const passedAnswers = answerMessages.filter((message) => message.evaluation?.userRating === "pass").length;
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
        setView("interview");
        setError(null);
        setCustomQuestion("");
        setQuickQuestion("");
        setUserNotes("");
        setSessionStatus("idle");
        onResetTestEvents?.();
        syncTestPanelStatus("idle");
        clearThinkingEvents();
        clearTagEvents();
        setUseStreamingMode(true);
        setShowTemplatePanel(false);
        setIsFullscreen(false);
        setLeftPanelCollapsed(isDeepTestMode && Boolean(selectedRole && selectedProvider));
    }, [
        clearTagEvents,
        clearThinkingEvents,
        isDeepTestMode,
        onResetTestEvents,
        selectedRole,
        selectedProvider,
        stopStream,
        syncTestPanelStatus,
    ]);
    useEffect(() => {
        return () => {
            void stopStream();
        };
    }, [stopStream]);
    useEffect(() => {
        if (!isDeepTestMode || isFullscreen)
            return;
        if (selectedRole && selectedProvider) {
            setLeftPanelCollapsed(true);
        }
    }, [isDeepTestMode, isFullscreen, selectedProvider, selectedRole]);
    useEffect(() => {
        if (!isFullscreen)
            return;
        const handleKeydown = (event) => {
            if (event.key === "Escape") {
                setIsFullscreen(false);
            }
        };
        window.addEventListener("keydown", handleKeydown);
        return () => window.removeEventListener("keydown", handleKeydown);
    }, [isFullscreen]);
    const buildContext = () => {
        return answerMessages.slice(-3).map((message) => ({
            question: message.question || "",
            answer: message.content,
        }));
    };
    const stringifyEventPayload = (payload, limit = 4000) => {
        try {
            const text = typeof payload === "string"
                ? payload
                : JSON.stringify(payload, null, 2);
            if (text.length <= limit)
                return text;
            return `${text.slice(0, limit)}...`;
        }
        catch {
            return String(payload);
        }
    };
    const clearSessionEvents = () => {
        onResetTestEvents?.();
        setSessionStatus("idle");
        syncTestPanelStatus("idle");
    };
    const handleSendQuestion = async (template, directQuestion) => {
        if (!selectedRole || !selectedProvider) {
            setError("请先选择岗位与模型");
            return;
        }
        const question = (template?.question ||
            directQuestion ||
            customQuestion).trim();
        if (!question)
            return;
        setError(null);
        clearThinkingEvents();
        clearTagEvents();
        setSessionStatus("running");
        syncTestPanelStatus("running");
        pushSessionEvent({
            type: "command",
            timestamp: new Date().toISOString(),
            content: `POST /v2/llm/interview/ask ${stringifyEventPayload({
                role: selectedRole,
                provider_id: selectedProvider,
                model: selectedModel,
                question,
            })}`,
        });
        pushSessionEvent({
            type: "stdout",
            timestamp: new Date().toISOString(),
            content: "发送面试问题...",
        });
        const questionMessage = {
            id: createMessageId(),
            type: "question",
            content: question,
            timestamp: new Date().toISOString(),
            sender: "user",
            questionId: template?.id,
            expectedCriteria: template?.expectedCriteria,
        };
        setMessages((prev) => [...prev, questionMessage]);
        if (!template && !directQuestion) {
            setCustomQuestion("");
        }
        if (directQuestion) {
            setQuickQuestion("");
        }
        if (isDeepTestMode) {
            setShowTemplatePanel(false);
        }
        setResponding(true);
        // Use streaming mode if enabled (for real-time output)
        if (useStreamingMode) {
            pushSessionEvent({
                type: "stdout",
                timestamp: new Date().toISOString(),
                content: "Using streaming mode for real-time output...",
            });
            const streamSessionId = sessionId || `interactive-${createMessageId()}`;
            if (!sessionId) {
                setSessionId(streamSessionId);
            }
            let envOverrides = null;
            if (resolveEnvOverrides) {
                try {
                    envOverrides = await resolveEnvOverrides(selectedProvider);
                }
                catch {
                    envOverrides = null;
                }
            }
            await startStream({
                roleId: selectedRole,
                providerId: selectedProvider,
                model: selectedModel || "",
                question,
                expectedCriteria: template?.expectedCriteria,
                expectsThinking: template ? template.difficulty !== "basic" : undefined,
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
                model: selectedModel || "",
                question,
                expectedCriteria: template?.expectedCriteria,
                expectsThinking: template ? template.difficulty !== "basic" : undefined,
                sessionId,
                context: buildContext(),
            });
            if (!response) {
                setResponding(false);
                setSessionStatus("failed");
                syncTestPanelStatus("failed");
                pushSessionEvent({
                    type: "error",
                    timestamp: new Date().toISOString(),
                    content: "未收到模型响应",
                });
                return;
            }
            if (response.sessionId && !sessionId) {
                setSessionId(response.sessionId);
            }
            pushSessionEvent({
                type: "response",
                timestamp: new Date().toISOString(),
                content: stringifyEventPayload(response),
            });
            // Debug output removed - streaming mode unified
            const answerMessage = {
                id: createMessageId(),
                type: "answer",
                content: response.answer || response.output || "",
                timestamp: new Date().toISOString(),
                sender: "model",
                questionId: template?.id,
                question,
                expectedCriteria: template?.expectedCriteria,
                thinking: response.thinking,
                evaluation: {
                    userRating: "pending",
                    notes: "",
                    criteriaAssessment: normalizeCriteriaAssessment(template?.expectedCriteria || []),
                },
            };
            setMessages((prev) => [...prev, answerMessage]);
            if (response.ok === false) {
                setError(response.error || "模型返回失败");
                setSessionStatus("failed");
                syncTestPanelStatus("failed");
                pushSessionEvent({
                    type: "error",
                    timestamp: new Date().toISOString(),
                    content: response.error || "模型返回失败",
                });
            }
            else {
                setSessionStatus("success");
                syncTestPanelStatus("success");
                pushSessionEvent({
                    type: "result",
                    timestamp: new Date().toISOString(),
                    content: "已收到模型响应",
                });
            }
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "发送问题失败");
            setSessionStatus("failed");
            syncTestPanelStatus("failed");
            pushSessionEvent({
                type: "error",
                timestamp: new Date().toISOString(),
                content: err instanceof Error ? err.message : "发送问题失败",
            });
        }
        finally {
            setResponding(false);
        }
    };
    const updateEvaluation = (messageId, updates) => {
        setMessages((prev) => prev.map((message) => {
            if (message.id !== messageId || message.type !== "answer") {
                return message;
            }
            return {
                ...message,
                evaluation: {
                    userRating: "pending",
                    notes: "",
                    criteriaAssessment: normalizeCriteriaAssessment(message.expectedCriteria || []),
                    ...(message.evaluation || {}),
                    ...updates,
                },
            };
        }));
    };
    const analyzePerformance = (answers) => {
        const stats = new Map();
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
            rate: value.total ? value.pass / value.total : 0,
        }));
        scored.sort((a, b) => b.rate - a.rate);
        const strengths = scored.slice(0, 3).map((item) => item.key);
        const weaknesses = scored
            .slice(-3)
            .map((item) => item.key)
            .filter(Boolean);
        return { strengths, weaknesses };
    };
    const buildReport = (overallStatus) => {
        const startTime = messages[0]?.timestamp || new Date().toISOString();
        const endTime = new Date().toISOString();
        const questions = answerMessages.map((message) => ({
            question: message.question || "",
            answer: message.content,
            evaluation: message.evaluation,
            expectedCriteria: message.expectedCriteria,
        }));
        const passedQuestions = answerMessages.filter((message) => message.evaluation?.userRating === "pass").length;
        const totalQuestions = answerMessages.length || 1;
        const { strengths, weaknesses } = analyzePerformance(answerMessages);
        const resolvedModel = resolveSelectedModel(selectedModel, activeProvider?.providerType, activeProvider?.model);
        return {
            id: sessionId || createMessageId(),
            role: selectedRole || "pm",
            provider: {
                id: selectedProvider || "",
                name: activeProvider?.name || selectedProvider || "未署名提供商",
                model: resolvedModel.model,
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
                recommendation: overallStatus === "passed" ? "建议通过面试" : "建议进一步提升后重试",
            },
            userNotes,
        };
    };
    const finalizeInterview = async (status) => {
        if (!selectedRole || !selectedProvider)
            return;
        setIsFullscreen(false);
        const nextReport = buildReport(status);
        setReport(nextReport);
        setView("report");
        setSaving(true);
        pushSessionEvent({
            type: "command",
            timestamp: new Date().toISOString(),
            content: `POST /v2/llm/interview/save ${stringifyEventPayload({
                role: selectedRole,
                provider_id: selectedProvider,
                model: selectedModel,
                status,
            })}`,
        });
        pushSessionEvent({
            type: "stdout",
            timestamp: new Date().toISOString(),
            content: "保存面试报告...",
        });
        try {
            const result = await onSaveReport({
                roleId: selectedRole,
                providerId: selectedProvider,
                model: selectedModel,
                report: nextReport,
            });
            if (result?.report_path) {
                setReportSavedPath(result.report_path);
                pushSessionEvent({
                    type: "result",
                    timestamp: new Date().toISOString(),
                    content: `报告已保存: ${result.report_path}`,
                });
            }
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "保存面试报告失败");
            pushSessionEvent({
                type: "error",
                timestamp: new Date().toISOString(),
                content: err instanceof Error ? err.message : "保存面试报告失败",
            });
        }
        finally {
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
        setView("interview");
        setError(null);
        setCustomQuestion("");
        setQuickQuestion("");
        setUserNotes("");
        setSessionStatus("idle");
        setUseStreamingMode(true);
        onResetTestEvents?.();
        syncTestPanelStatus("idle");
        clearThinkingEvents();
        clearTagEvents();
        setStreamingThinking("");
        setStreamingAnswer("");
        setIsThinkingActive(false);
        setIsAnswerActive(false);
        setShowTemplatePanel(false);
        setIsFullscreen(false);
        setLeftPanelCollapsed(isDeepTestMode && Boolean(selectedRole && selectedProvider));
    };
    if (view === "report" && report) {
        return (_jsxs("div", { "data-testid": "llm-interactive-report", className: "soft-panel rounded-xl p-5 space-y-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs uppercase tracking-wide text-text-dim", children: "\u4EA4\u4E92\u5F0F\u9762\u8BD5\u62A5\u544A" }), _jsxs("h3", { className: "text-lg font-semibold text-text-main", children: ["\u9762\u8BD5\u62A5\u544A \u00B7 ", activeRole?.label || report.role] }), _jsxs("div", { className: "text-[11px] text-text-dim", children: ["\u6A21\u578B\uFF1A", report.provider.name, " \u2022 ", report.provider.model] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [report.overallStatus === "passed" ? (_jsx("span", { className: "px-2 py-1 text-[10px] uppercase font-semibold rounded border bg-emerald-500/[0.12] text-emerald-200 border-emerald-500/25", children: "\u901A\u8FC7" })) : (_jsx("span", { className: "px-2 py-1 text-[10px] uppercase font-semibold rounded border bg-amber-500/[0.12] text-amber-200 border-amber-500/25", children: "\u5931\u8D25" })), _jsxs("button", { "data-testid": "llm-interactive-report-reset", onClick: resetInterview, className: "px-3 py-1.5 text-[10px] soft-chip rounded hover:border-white/20 flex items-center gap-1", children: [_jsx(RefreshCw, { className: "size-3" }), "\u91CD\u65B0\u5F00\u59CB"] })] })] }), saving ? (_jsxs("div", { className: "text-[11px] text-text-dim flex items-center gap-2", children: [_jsx(Loader2, { className: "size-3 animate-spin" }), "\u6B63\u5728\u4FDD\u5B58\u9762\u8BD5\u62A5\u544A..."] })) : reportSavedPath ? (_jsxs("div", { "data-testid": "llm-interactive-report-saved-path", className: "text-[11px] text-emerald-300", children: ["\u62A5\u544A\u5DF2\u4FDD\u5B58\uFF1A", reportSavedPath] })) : null, error ? (_jsx("div", { className: "text-[11px] text-rose-300 bg-rose-500/[0.08] border border-rose-500/20 rounded p-2", children: error })) : null, _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-3 gap-3 text-xs", children: [_jsxs("div", { className: "soft-inset rounded-lg p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u603B\u95EE\u9898\u6570" }), _jsx("div", { className: "text-text-main font-semibold", children: report.summary.totalQuestions })] }), _jsxs("div", { className: "soft-inset rounded-lg p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u901A\u8FC7\u95EE\u9898" }), _jsx("div", { className: "text-text-main font-semibold", children: report.summary.passedQuestions })] }), _jsxs("div", { className: "soft-inset rounded-lg p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u5E73\u5747\u8BC4\u5206" }), _jsxs("div", { className: "text-text-main font-semibold", children: [Math.round(report.summary.averageRating * 100), "%"] })] })] }), _jsxs("div", { className: "soft-panel-subtle rounded-xl p-4 text-xs space-y-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim", children: "\u603B\u7ED3" }), _jsxs("div", { className: "text-text-main", children: ["\u63A8\u8350\uFF1A", report.summary.recommendation] }), report.summary.strengths.length > 0 ? (_jsxs("div", { className: "text-text-dim", children: ["\u4F18\u52BF\uFF1A", report.summary.strengths.join("、")] })) : null, report.summary.weaknesses.length > 0 ? (_jsxs("div", { className: "text-text-dim", children: ["\u5F85\u63D0\u5347\uFF1A", report.summary.weaknesses.join("、")] })) : null, report.userNotes ? (_jsxs("div", { className: "text-text-dim", children: ["\u5907\u6CE8\uFF1A", report.userNotes] })) : null] })] }));
    }
    return (_jsxs("div", { "data-testid": "llm-interactive-hall", className: `relative ${isFullscreen
            ? "fixed inset-2 z-[70] flex min-h-0 flex-col gap-2 soft-panel rounded-xl p-2"
            : "flex h-full min-h-0 min-w-0 flex-1 basis-0 flex-col gap-2 overflow-hidden"}`, children: [_jsx("div", { className: `soft-panel rounded-xl px-3 ${compactMode ? "py-1" : "py-2"}`, children: _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsx("div", { className: "min-w-0", children: _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2 text-[10px]", children: [_jsx("span", { className: "font-semibold text-text-main", children: "\u4EA4\u4E92\u5F0F\u9762\u8BD5" }), _jsxs("span", { className: "truncate text-text-dim", children: [activeRole?.label || "未选择", " /", " ", activeProvider?.name || "未选择", selectedModel ? ` / ${selectedModel}` : ""] })] }) }), _jsxs("div", { className: "flex flex-wrap items-center gap-2 text-[10px]", children: [_jsx("span", { className: `px-2 py-1 rounded border uppercase tracking-wide ${SESSION_STATUS[sessionStatus].badge}`, children: SESSION_STATUS[sessionStatus].label }), _jsx("span", { className: `px-2 py-1 rounded border ${isStreamConnecting
                                        ? "border-amber-400/30 bg-amber-500/[0.12] text-amber-100"
                                        : "border-white/[0.08] bg-white/[0.04] text-text-dim"}`, children: isStreamConnecting ? "流连接中" : "流连接空闲" }), _jsxs("label", { className: "flex items-center gap-1.5 soft-chip rounded px-2 py-1 text-text-dim", children: [_jsx("input", { type: "checkbox", checked: useStreamingMode, onChange: (event) => setUseStreamingMode(event.target.checked), className: "h-3 w-3 rounded border-white/[0.15] bg-white/5" }), "\u5B9E\u65F6\u6D41\u5F0F\u89E3\u6790"] }), _jsx("button", { type: "button", onClick: clearSessionEvents, className: "px-2 py-1 soft-chip rounded hover:border-white/20 text-text-dim hover:text-text-main", children: "\u6E05\u7A7A\u65E5\u5FD7" }), isDeepTestMode ? (_jsxs(_Fragment, { children: [!isFullscreen ? (_jsx("button", { type: "button", onClick: () => setShowTemplatePanel((prev) => !prev), className: `px-2 py-1 rounded border transition-colors ${showTemplatePanel
                                                ? "border-amber-400/30 bg-amber-500/[0.12] text-amber-100"
                                                : "border-white/[0.08] text-text-dim hover:border-white/20 hover:text-text-main"}`, children: showTemplatePanel ? "隐藏模板" : "显示模板" })) : null, !isFullscreen ? (_jsx("button", { type: "button", "data-testid": "llm-interactive-sidebar-toggle", onClick: () => setLeftPanelCollapsed((prev) => !prev), className: "px-2 py-1 rounded border border-white/[0.08] text-text-dim hover:border-white/20 hover:text-text-main", children: leftPanelCollapsed ? "展开侧栏" : "收起侧栏" })) : null, _jsxs("button", { type: "button", "data-testid": "llm-interactive-fullscreen-toggle", onClick: () => setIsFullscreen((prev) => !prev), className: "px-2 py-1 rounded border border-white/[0.12] bg-white/[0.06] text-text-main hover:bg-white/10 inline-flex items-center gap-1", title: isFullscreen ? "退出全屏（Esc）" : "进入全屏", children: [isFullscreen ? (_jsx(Minimize2, { className: "size-3" })) : (_jsx(Maximize2, { className: "size-3" })), isFullscreen ? "退出全屏" : "全屏"] })] })) : null] })] }) }), _jsxs("div", { className: `grid min-h-0 min-w-0 flex-1 basis-0 overflow-hidden ${isFullscreen
                    ? "grid-cols-1 gap-2"
                    : isDeepTestMode
                        ? "grid-cols-1 xl:grid-cols-[minmax(160px,0.72fr)_minmax(0,2.28fr)] gap-3"
                        : "grid-cols-1 xl:grid-cols-[1.05fr_1.95fr_1.15fr] gap-5"}`, children: [showLeftPanel ? (_jsxs("div", { className: `${compactMode ? "grid grid-rows-[auto_1fr] gap-2" : "grid grid-rows-[auto_1fr_auto_1fr] gap-4"} min-h-0 min-w-0 overflow-hidden`, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\u9762\u8BD5\u5C97\u4F4D" }), isDeepTestMode ? (_jsx("button", { type: "button", "data-testid": "llm-interactive-sidebar-toggle", onClick: () => setLeftPanelCollapsed((prev) => !prev), className: "px-2 py-1 text-[10px] rounded border border-white/[0.08] text-text-dim hover:border-white/20", children: leftPanelCollapsed ? "展开" : "收起" })) : null] }), leftPanelCollapsed && isDeepTestMode ? (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "soft-inset rounded-lg p-2 text-[10px]", children: [_jsx("div", { className: "text-text-dim", children: "\u5C97\u4F4D" }), _jsx("div", { className: "text-text-main mt-1", children: activeRole?.label || "未选择" })] }), _jsxs("div", { className: "soft-inset rounded-lg p-2 text-[10px]", children: [_jsx("div", { className: "text-text-dim", children: "\u63D0\u4F9B\u5546" }), _jsx("div", { className: "text-text-main mt-1", children: activeProvider?.name || "未选择" }), _jsx("div", { className: "text-text-dim mt-1", children: selectedModel || activeProvider?.model || "未设置模型" })] })] })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "space-y-2 min-h-0 overflow-auto pr-1", children: roles.map((role) => {
                                            const isActive = role.id === selectedRole;
                                            const badge = getLlmRoleDefinition(role.id).badge;
                                            return (_jsxs("button", { "data-testid": `llm-interview-role-${role.id}`, onClick: () => onSelectRole(role.id), className: `w-full text-left rounded-xl border ${compactMode ? "p-2.5" : "p-4"} transition-all ${isActive
                                                    ? "soft-raised border-white/[0.15]"
                                                    : "border-white/[0.08] bg-white/[0.04] hover:border-white/[0.15]"}`, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: `px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`, children: role.label }), role.readiness?.ready ? (_jsx(CheckCircle2, { className: "size-4 text-emerald-400" })) : (_jsx(AlertTriangle, { className: "size-4 text-amber-300" }))] }), !compactMode ? (_jsx("div", { className: "text-[10px] text-text-dim uppercase tracking-wide", children: role.requiresThinking ? "需要思考" : "可选思考" })) : null] }), !compactMode ? (_jsx("div", { className: "mt-2 text-xs text-text-dim", children: role.description })) : null] }, role.id));
                                        }) }), _jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\u6A21\u578B\u9009\u62E9" }), _jsx("div", { className: "space-y-2 min-h-0 overflow-auto pr-1", children: providers.map((provider) => {
                                            const isActive = provider.id === selectedProvider;
                                            const styles = STATUS_STYLES[provider.status] || STATUS_STYLES.untested;
                                            return (_jsx("button", { "data-testid": `llm-interview-provider-${provider.id}`, onClick: () => onSelectProvider(provider.id), className: `w-full min-w-0 text-left rounded-xl border ${compactMode ? "p-2.5" : "p-3"} transition-all ${isActive
                                                    ? "soft-raised border-white/[0.15]"
                                                    : `${styles.border} ${styles.bg} hover:border-white/[0.15]`}`, children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0", children: [_jsx("div", { className: "text-xs text-text-main font-semibold truncate", children: provider.name }), provider.interviewStatus &&
                                                                    provider.interviewStatus !== "none" ? (_jsx("span", { className: `text-[9px] uppercase px-1.5 py-0.5 rounded border ${provider.interviewStatus === "passed"
                                                                        ? "border-emerald-500/25 bg-emerald-500/[0.08] text-emerald-300"
                                                                        : "border-rose-500/25 bg-rose-500/[0.08] text-rose-300"}`, children: provider.interviewStatus === "passed"
                                                                        ? "面试通过"
                                                                        : "面试失败" })) : null] }), _jsxs("div", { className: "flex items-center gap-2", children: [!compactMode ? (_jsx("span", { className: "text-[10px] text-text-dim", children: provider.model || "未设置模型" })) : null, _jsx("span", { className: `text-[9px] uppercase px-2 py-0.5 rounded border ${styles.border} ${styles.text}`, children: STATUS_LABELS[provider.status] })] })] }) }, provider.id));
                                        }) })] }))] })) : null, showTemplateColumn ? (_jsxs("div", { className: "flex min-h-0 min-w-0 flex-col gap-4 overflow-hidden xl:order-3", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\u95EE\u9898\u6A21\u677F\u5E93" }), _jsx("div", { className: "space-y-3 flex-1 min-h-0 overflow-auto pr-1", children: templatesByCategory.length === 0 ? (_jsx("div", { className: "rounded-xl border border-white/10 bg-white/5 p-4 text-xs text-text-dim", children: "\u8BF7\u9009\u62E9\u5C97\u4F4D\u4EE5\u663E\u793A\u5BF9\u5E94\u95EE\u9898\u6A21\u677F\u3002" })) : (templatesByCategory.map(([category, templates]) => (_jsxs("div", { className: "soft-chip rounded-xl p-3 space-y-2", children: [_jsx("div", { className: "text-[11px] font-semibold text-text-main", children: category }), _jsx("div", { className: "space-y-2", children: templates.map((template) => (_jsxs("button", { onClick: () => handleSendQuestion(template), disabled: responding, className: "w-full text-left text-[11px] px-3 py-2 soft-inset rounded hover:border-white/[0.15] disabled:opacity-60", children: [_jsx("div", { className: "text-text-main font-semibold", children: template.title }), _jsx("div", { className: "text-text-dim mt-1", children: template.question })] }, template.id))) })] }, category)))) }), _jsxs("div", { className: "soft-panel-subtle rounded-xl p-4 space-y-3", children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\u81EA\u5B9A\u4E49\u95EE\u9898" }), _jsx("textarea", { value: customQuestion, onChange: (event) => setCustomQuestion(event.target.value), placeholder: "\u8F93\u5165\u81EA\u5B9A\u4E49\u9762\u8BD5\u95EE\u9898...", rows: 3, className: "w-full rounded-lg soft-inset p-2 text-xs text-text-main" }), _jsxs("button", { onClick: () => handleSendQuestion(), disabled: responding || !customQuestion.trim(), className: "w-full px-3 py-2 text-[11px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(Send, { className: "size-3" }), responding ? "发送中..." : "发送问题"] })] })] })) : null, _jsxs("div", { className: "flex min-h-0 min-w-0 flex-1 basis-0 flex-col overflow-hidden xl:order-2", children: [_jsxs("div", { "data-testid": "llm-interactive-center", className: `flex min-h-0 min-w-0 flex-1 basis-0 flex-col overflow-y-auto soft-panel-subtle rounded-xl custom-scrollbar ${compactMode ? "gap-1.5 p-1.5" : "gap-3 p-4"}`, children: [_jsxs("div", { className: `${compactMode ? "sr-only" : "flex"} shrink-0 items-center justify-between gap-2 text-[11px] text-text-dim`, children: [_jsx("span", { className: "font-semibold text-text-main", children: "\u5B9E\u65F6\u5BF9\u8BDD\u533A" }), _jsxs("span", { children: [messages.length, " \u6761\u6D88\u606F"] })] }), showThinkingPanel ? (_jsxs("div", { "data-testid": "llm-interactive-stream-monitors", className: `grid min-h-[84px] min-w-0 shrink-0 gap-2 overflow-hidden ${compactMode
                                            ? "max-h-[120px] grid-cols-1 lg:max-h-[96px] lg:grid-cols-2"
                                            : "max-h-[260px] grid-cols-1 lg:max-h-[180px] lg:grid-cols-2"}`, children: [_jsx(RealtimeThinkingDisplay, { events: displayedThinkingEvents, enabled: thinkingEnabled, isStreaming: responding && thinkingEnabled, onClear: clearThinkingEvents, dense: compactMode, className: compactMode ? "h-full min-h-0" : "min-h-[140px]" }), _jsx(StreamingTags, { events: tagEvents, isStreaming: responding, onClear: clearTagEvents, dense: compactMode, className: compactMode ? "h-full min-h-0" : "min-h-[140px]" })] })) : null, messages.length === 0 ? (_jsx("div", { "data-testid": "llm-interactive-messages", className: "flex min-h-[120px] min-w-0 flex-1 items-center justify-center soft-inset rounded-lg text-xs text-text-dim", children: "\u6682\u65E0\u5BF9\u8BDD\u8BB0\u5F55\uFF0C\u8BF7\u5148\u9009\u62E9\u6A21\u677F\u95EE\u9898\u6216\u8F93\u5165\u81EA\u5B9A\u4E49\u95EE\u9898\u3002" })) : (_jsxs("div", { "data-testid": "llm-interactive-messages", className: `min-h-[120px] min-w-0 flex-1 overflow-y-auto overscroll-contain soft-inset rounded-lg custom-scrollbar ${compactMode ? "space-y-2 p-2 pr-1" : "space-y-3 p-3 pr-2"}`, children: [qaPairs.map((pair, index) => {
                                                const question = pair.question;
                                                const answer = pair.answer;
                                                const criteria = answer?.expectedCriteria ||
                                                    question?.expectedCriteria ||
                                                    [];
                                                return (_jsxs("div", { className: `soft-chip rounded-xl text-xs flex-shrink-0 ${compactMode ? "p-2.5 space-y-2" : "p-4 space-y-3"}`, children: [_jsxs("div", { className: "text-[10px] uppercase tracking-wide text-text-dim", children: ["\u95EE\u7B54 ", index + 1] }), question ? (_jsxs("div", { className: `soft-inset rounded-lg ${compactMode ? "p-2" : "p-3"}`, children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u63D0\u95EE" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap break-words", children: question.content })] })) : null, answer ? (_jsxs("div", { className: `soft-inset rounded-lg ${compactMode ? "p-2 space-y-1.5" : "p-3 space-y-2"}`, children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u4F5C\u7B54" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap break-words", children: answer.content }), answer.thinking ? (_jsxs("div", { className: "text-[11px] text-text-dim whitespace-pre-wrap break-words", children: [_jsx("span", { className: "text-[10px] uppercase tracking-wide", children: "\u601D\u8003\u94FE" }), _jsx("div", { children: answer.thinking })] })) : null, _jsxs("div", { className: "space-y-2 pt-2 border-t border-white/10", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs("button", { "data-testid": "llm-interactive-answer-pass", onClick: () => updateEvaluation(answer.id, {
                                                                                        userRating: "pass",
                                                                                    }), className: `px-2 py-1 text-[10px] rounded border flex items-center gap-1 ${answer.evaluation?.userRating === "pass"
                                                                                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                                                                                        : "border-white/[0.08] text-text-dim hover:border-emerald-500/25"}`, children: [_jsx(Check, { className: "size-3" }), "\u901A\u8FC7"] }), _jsxs("button", { "data-testid": "llm-interactive-answer-fail", onClick: () => updateEvaluation(answer.id, {
                                                                                        userRating: "fail",
                                                                                    }), className: `px-2 py-1 text-[10px] rounded border flex items-center gap-1 ${answer.evaluation?.userRating === "fail"
                                                                                        ? "border-rose-500/30 bg-rose-500/10 text-rose-200"
                                                                                        : "border-white/[0.08] text-text-dim hover:border-rose-500/25"}`, children: [_jsx(XCircle, { className: "size-3" }), "\u5931\u8D25"] })] }), criteria.length > 0 ? (_jsxs("div", { className: "space-y-1 text-[10px] text-text-dim", children: [_jsx("div", { className: "uppercase tracking-wide text-[10px]", children: "\u8BC4\u4F30\u6307\u6807" }), _jsx("div", { className: "flex flex-wrap gap-x-3 gap-y-1", children: criteria.map((item) => (_jsxs("label", { className: "flex items-center gap-1 cursor-pointer hover:text-text-main", children: [_jsx("input", { type: "checkbox", checked: Boolean(answer.evaluation
                                                                                                    ?.criteriaAssessment?.[item]), onChange: (event) => {
                                                                                                    updateEvaluation(answer.id, {
                                                                                                        criteriaAssessment: {
                                                                                                            ...(answer.evaluation
                                                                                                                ?.criteriaAssessment || {}),
                                                                                                            [item]: event.target.checked,
                                                                                                        },
                                                                                                    });
                                                                                                }, className: "h-3 w-3 rounded border-white/[0.15] bg-white/5" }), _jsx("span", { className: "break-words", children: item })] }, item))) })] })) : null, _jsx("input", { value: answer.evaluation?.notes || "", onChange: (event) => updateEvaluation(answer.id, {
                                                                                notes: event.target.value,
                                                                            }), placeholder: "\u5907\u6CE8\uFF08\u53EF\u9009\uFF09", className: "w-full rounded border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-text-main" })] })] })) : (_jsx("div", { className: "text-[11px] text-text-dim", children: responding && !hasStreamingContent
                                                                ? "等待回答中..."
                                                                : !responding
                                                                    ? "暂无回答"
                                                                    : null })), !answer &&
                                                            responding &&
                                                            hasStreamingContent &&
                                                            index === qaPairs.length - 1 ? (_jsxs("div", { className: "space-y-2", children: [streamingThinking || isThinkingActive ? (_jsxs("div", { className: `soft-inset rounded-lg ${compactMode ? "p-2" : "p-3"}`, children: [_jsxs("div", { className: "flex items-center gap-2 mb-1.5", children: [_jsx(Brain, { className: "size-3 text-amber-300" }), _jsx("span", { className: "text-[10px] uppercase tracking-wide text-amber-300 font-semibold", children: "Architect thinking aloud" }), isThinkingActive ? (_jsx(Loader2, { className: "size-3 text-amber-300 animate-spin" })) : null] }), _jsxs("div", { className: "text-text-main text-xs whitespace-pre-wrap break-words leading-relaxed", children: [streamingThinking, isThinkingActive ? (_jsx("span", { className: "ml-0.5 inline-block h-3.5 w-0.5 bg-amber-400/70 align-middle" })) : null] })] })) : null, streamingAnswer || isAnswerActive ? (_jsxs("div", { className: `soft-inset rounded-lg ${compactMode ? "p-2" : "p-3"}`, children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-dim mb-1", children: "\u4F5C\u7B54\u4E2D" }), _jsxs("div", { className: "text-text-main text-xs whitespace-pre-wrap break-words leading-relaxed", children: [streamingAnswer, isAnswerActive ? (_jsx("span", { className: "ml-0.5 inline-block h-3.5 w-0.5 bg-emerald-400/70 align-middle" })) : null] })] })) : null] })) : null] }, question?.id || answer?.id || `qa-${index}`));
                                            }), _jsx("div", { ref: conversationEndRef })] })), error ? (_jsx("div", { className: "text-[10px] text-rose-300 bg-rose-500/[0.08] border border-rose-500/20 rounded p-2 flex-shrink-0", children: error })) : null, _jsxs("div", { "data-testid": "llm-interactive-composer", className: `soft-inset rounded-lg flex-shrink-0 ${compactMode ? "grid grid-cols-[minmax(0,1fr)_120px] items-end gap-2 p-2" : "p-3 space-y-2"}`, children: [_jsx("div", { className: compactMode
                                                    ? "sr-only"
                                                    : "text-[10px] uppercase tracking-wide text-text-dim", children: "\u7EE7\u7EED\u8FFD\u95EE" }), _jsx("textarea", { "data-testid": "llm-interactive-quick-question", value: quickQuestion, onChange: (event) => setQuickQuestion(event.target.value), placeholder: "\u5728\u8FD9\u91CC\u8F93\u5165\u8FFD\u95EE\u95EE\u9898...", rows: compactMode ? 1 : 2, className: "w-full rounded border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-text-main resize-none" }), _jsxs("button", { "data-testid": "llm-interactive-send-question", onClick: () => handleSendQuestion(undefined, quickQuestion), disabled: responding || !quickQuestion.trim(), className: `w-full px-3 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1 ${compactMode ? "py-1.5" : "py-1.5"}`, children: [_jsx(Send, { className: "size-3" }), responding ? "发送中..." : "发送追问"] })] }), !isFullscreen && compactMode ? (_jsxs("div", { "data-testid": "llm-interactive-finalize-controls", className: "flex shrink-0 flex-wrap items-center gap-2 soft-inset rounded-lg p-1.5", children: [_jsx("input", { value: userNotes, onChange: (event) => setUserNotes(event.target.value), placeholder: "\u9762\u8BD5\u5B98\u5907\u6CE8", className: "min-w-[180px] flex-1 rounded border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-text-main" }), _jsxs("button", { "data-testid": "llm-interactive-finalize-pass", onClick: () => finalizeInterview("passed"), disabled: !canFinalize || passedAnswers === 0, className: "px-3 py-1 text-[10px] font-semibold bg-emerald-500/[0.15] hover:bg-emerald-500/25 text-emerald-200 rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(CheckCircle2, { className: "size-3" }), "\u901A\u8FC7"] }), _jsxs("button", { "data-testid": "llm-interactive-finalize-fail", onClick: () => finalizeInterview("failed"), disabled: answerMessages.length === 0 || responding, className: "px-3 py-1 text-[10px] font-semibold bg-rose-500/[0.15] hover:bg-rose-500/25 text-rose-200 rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(XCircle, { className: "size-3" }), "\u5931\u8D25"] }), _jsxs("button", { "data-testid": "llm-interactive-reset", onClick: resetInterview, className: "px-3 py-1 text-[10px] border border-white/[0.08] rounded hover:border-white/20 flex items-center justify-center gap-1", children: [_jsx(RefreshCw, { className: "size-3" }), "\u91CD\u7F6E"] })] })) : null] }), !isFullscreen && !compactMode ? (_jsxs("div", { "data-testid": "llm-interactive-finalize-controls", className: `soft-panel-subtle rounded-xl flex-shrink-0 ${compactMode ? "p-2.5 space-y-2" : "p-4 space-y-3"}`, children: [_jsx("div", { className: "text-xs font-semibold text-text-main uppercase tracking-wide", children: "\u9762\u8BD5\u63A7\u5236" }), _jsx("textarea", { value: userNotes, onChange: (event) => setUserNotes(event.target.value), placeholder: "\u9762\u8BD5\u5B98\u5907\u6CE8\uFF08\u53EF\u9009\uFF09", rows: compactMode ? 1 : 2, className: "w-full rounded-lg soft-inset p-2 text-xs text-text-main resize-none" }), _jsxs("div", { className: `flex ${compactMode ? "flex-row flex-wrap" : "flex-col"} gap-2`, children: [_jsxs("button", { "data-testid": "llm-interactive-finalize-pass", onClick: () => finalizeInterview("passed"), disabled: !canFinalize || passedAnswers === 0, className: `px-3 ${compactMode ? "py-1.5 text-[10px]" : "py-2 text-[11px]"} font-semibold bg-emerald-500/[0.15] hover:bg-emerald-500/25 text-emerald-200 rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1`, children: [_jsx(CheckCircle2, { className: "size-3" }), "\u901A\u8FC7"] }), _jsxs("button", { "data-testid": "llm-interactive-finalize-fail", onClick: () => finalizeInterview("failed"), disabled: answerMessages.length === 0 || responding, className: `px-3 ${compactMode ? "py-1.5 text-[10px]" : "py-2 text-[11px]"} font-semibold bg-rose-500/[0.15] hover:bg-rose-500/25 text-rose-200 rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1`, children: [_jsx(XCircle, { className: "size-3" }), "\u5931\u8D25"] }), _jsxs("button", { "data-testid": "llm-interactive-reset", onClick: resetInterview, className: `px-3 ${compactMode ? "py-1.5 text-[10px]" : "py-2 text-[11px]"} border border-white/[0.08] rounded hover:border-white/20 flex items-center justify-center gap-1`, children: [_jsx(RefreshCw, { className: "size-3" }), "\u91CD\u7F6E\u4F1A\u8BDD"] })] })] })) : null] })] }), showFloatingTemplatePanel ? (_jsxs("div", { className: "absolute right-2 top-14 z-40 w-[min(420px,94vw)] soft-panel rounded-xl p-3", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-main", children: "\u6A21\u677F\u95EE\u9898\u9762\u677F" }), _jsx("button", { type: "button", onClick: () => setShowTemplatePanel(false), className: "px-2 py-1 soft-chip rounded text-[10px] text-text-dim hover:text-text-main", children: "\u5173\u95ED" })] }), _jsx("div", { className: "space-y-2 max-h-64 overflow-y-auto pr-1", children: templatesByCategory.length === 0 ? (_jsx("div", { className: "rounded border border-white/10 bg-white/5 p-2 text-[10px] text-text-dim", children: "\u8BF7\u9009\u62E9\u5C97\u4F4D\u4EE5\u663E\u793A\u5BF9\u5E94\u95EE\u9898\u6A21\u677F\u3002" })) : (templatesByCategory.map(([category, templates]) => (_jsxs("div", { className: "soft-inset rounded p-2 space-y-2", children: [_jsx("div", { className: "text-[10px] font-semibold text-text-main", children: category }), templates.map((template) => (_jsxs("button", { onClick: () => handleSendQuestion(template), disabled: responding, className: "w-full text-left text-[10px] px-2 py-1.5 soft-chip rounded hover:border-white/[0.15] disabled:opacity-60", children: [_jsx("div", { className: "text-text-main", children: template.title }), _jsx("div", { className: "text-text-dim mt-1", children: template.question })] }, template.id)))] }, category)))) }), _jsxs("div", { className: "mt-2 soft-inset rounded p-2 space-y-2", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-text-main", children: "\u81EA\u5B9A\u4E49\u95EE\u9898" }), _jsx("textarea", { value: customQuestion, onChange: (event) => setCustomQuestion(event.target.value), placeholder: "\u8F93\u5165\u81EA\u5B9A\u4E49\u95EE\u9898...", rows: 2, className: "w-full rounded border border-white/[0.08] bg-white/[0.04] p-2 text-[10px] text-text-main resize-none" }), _jsxs("button", { onClick: () => handleSendQuestion(), disabled: responding || !customQuestion.trim(), className: "w-full px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1", children: [_jsx(Send, { className: "size-3" }), responding ? "发送中..." : "发送问题"] })] })] })) : null, isFullscreen ? (_jsxs("div", { className: "absolute right-2 bottom-2 z-50 soft-panel rounded-xl p-2 flex items-center gap-2", children: [_jsxs("button", { type: "button", "data-testid": "llm-interactive-fullscreen-exit", onClick: () => setIsFullscreen(false), className: "px-2 py-1 text-[10px] rounded border border-white/[0.12] text-text-main hover:bg-white/10 inline-flex items-center gap-1", title: "\u9000\u51FA\u5168\u5C4F\uFF08Esc\uFF09", children: [_jsx(Minimize2, { className: "size-3" }), "\u9000\u51FA\u5168\u5C4F"] }), _jsx("button", { type: "button", "data-testid": "llm-interactive-finalize-pass", onClick: () => finalizeInterview("passed"), disabled: !canFinalize || passedAnswers === 0, className: "px-2 py-1 text-[10px] rounded border border-emerald-500/30 text-emerald-200 bg-emerald-500/10 disabled:opacity-60", children: "\u901A\u8FC7" }), _jsx("button", { type: "button", "data-testid": "llm-interactive-finalize-fail", onClick: () => finalizeInterview("failed"), disabled: answerMessages.length === 0 || responding, className: "px-2 py-1 text-[10px] rounded border border-rose-500/30 text-rose-200 bg-rose-500/10 disabled:opacity-60", children: "\u5931\u8D25" }), _jsx("button", { type: "button", "data-testid": "llm-interactive-reset", onClick: resetInterview, className: "px-2 py-1 text-[10px] rounded border border-white/[0.08] text-text-dim hover:border-white/20", children: "\u91CD\u7F6E" })] })) : null] }));
}
