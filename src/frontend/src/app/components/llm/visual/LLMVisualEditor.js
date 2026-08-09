import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, MiniMap, ReactFlow, } from "@xyflow/react";
import { Activity, ChevronDown, ChevronUp, LayoutGrid, Maximize, SlidersHorizontal, Trash2, Unplug, } from "lucide-react";
import "@xyflow/react/dist/style.css";
import { devLogger } from "@/app/utils/devLogger";
import { useVisualLLMConfig } from "./hooks/useVisualLLMConfig";
import { nodeTypes, edgeTypes } from "./utils/nodeTypes";
import { validateVisualGraph, isValidVisualConnection } from "./utils/validation";
import { extractNodePositions, extractNodeStates, getRoleBindings, updateProviderConcurrency, updateRoleBindingConcurrency, updateRoleConcurrency, } from "./utils/configConverter";
import { getLlmRoleDefinition, isKnownLlmRoleId } from "../roleDefinitions";
import { ValidationPanel, ValidationBadge } from "./components/ValidationPanel";
const isVisualRoleId = (value) => isKnownLlmRoleId(value);
const readPositiveInt = (value) => {
    const trimmed = value.trim();
    if (!trimmed)
        return undefined;
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
};
function extractLayoutWithFallback(nodes) {
    const layout = extractNodePositions(nodes);
    if (Object.keys(layout).length > 0) {
        return layout;
    }
    const fallback = {};
    nodes.forEach((node) => {
        const rawNode = node;
        const absolute = rawNode.positionAbsolute;
        if (absolute &&
            typeof absolute.x === "number" &&
            typeof absolute.y === "number") {
            fallback[node.id] = { x: absolute.x, y: absolute.y };
            return;
        }
        if (typeof rawNode.positionAbsoluteX === "number" &&
            typeof rawNode.positionAbsoluteY === "number") {
            fallback[node.id] = {
                x: rawNode.positionAbsoluteX,
                y: rawNode.positionAbsoluteY,
            };
        }
    });
    return fallback;
}
export function LLMVisualEditor({ config, status, onConfigChange, onSave, }) {
    const editorContainerRef = useRef(null);
    // Validation
    const validation = useMemo(() => {
        if (!config)
            return { valid: true, issues: [] };
        return validateVisualGraph(config);
    }, [config]);
    const { nodes, edges, onNodesChange, onEdgesChange, onNodesDelete, onEdgesDelete, onConnect, addModel, clearRoleAssignment, deleteNode, deleteEdge, setNodes, getCurrentConfig, } = useVisualLLMConfig({ config, status, onConfigChange });
    const [rfInstance, setRfInstance] = useState(null);
    const [modelDraft, setModelDraft] = useState("");
    const [providerDraft, setProviderDraft] = useState("");
    const [showAddModel, setShowAddModel] = useState(false);
    const [showConcurrencyPanel, setShowConcurrencyPanel] = useState(false);
    const [contextMenu, setContextMenu] = useState(null);
    const [showValidationPanel, setShowValidationPanel] = useState(true);
    const providers = useMemo(() => Object.entries(config?.providers || {}), [config]);
    const roleConcurrencyRows = useMemo(() => {
        return Object.entries(config?.roles || {})
            .filter(([roleId]) => isVisualRoleId(roleId))
            .map(([roleId, roleCfg]) => ({
            roleId: roleId,
            roleCfg,
            bindings: getRoleBindings(roleCfg),
        }))
            .filter((row) => row.bindings.length > 0 ||
            row.roleCfg.provider_id ||
            row.roleCfg.model);
    }, [config]);
    const bindingRows = useMemo(() => roleConcurrencyRows.flatMap((row) => row.bindings.map((binding, index) => ({
        roleId: row.roleId,
        binding,
        index,
    }))), [roleConcurrencyRows]);
    const commitConfigUpdate = useCallback((updater) => {
        const current = getCurrentConfig() || config;
        if (!current || !onConfigChange)
            return;
        const next = updater(current);
        onConfigChange(next);
    }, [config, getCurrentConfig, onConfigChange]);
    // --- Standard ReactFlow context-menu handlers (simplified, robust) ---
    // Previously used a complex capture-phase handler + DOM traversal + proximity
    // detection. That over-engineering caused the handlers to interfere with each
    // other, breaking right-click on edges. Now using ONLY ReactFlow's native
    // onEdgeContextMenu / onNodeContextMenu / onPaneContextMenu — the standard,
    // well-tested approach. CustomEdge uses interactionWidth={40} for a wide hit
    // area so ReactFlow's edge detection is reliable.
    const onNodeContextMenu = useCallback((event, node) => {
        event.preventDefault();
        const rect = editorContainerRef.current?.getBoundingClientRect();
        setContextMenu({
            visible: true,
            x: rect ? event.clientX - rect.left : event.clientX,
            y: rect ? event.clientY - rect.top : event.clientY,
            type: "node",
            data: node,
        });
    }, []);
    const onEdgeContextMenu = useCallback((event, edge) => {
        event.preventDefault();
        event.stopPropagation();
        const rect = editorContainerRef.current?.getBoundingClientRect();
        setContextMenu({
            visible: true,
            x: rect ? event.clientX - rect.left : event.clientX,
            y: rect ? event.clientY - rect.top : event.clientY,
            type: "edge",
            data: edge,
        });
    }, []);
    const onPaneContextMenu = useCallback((event) => {
        event.preventDefault();
        setContextMenu(null);
    }, []);
    const onPaneClick = useCallback(() => {
        setContextMenu(null);
    }, []);
    const closeContextMenu = useCallback(() => {
        setContextMenu(null);
    }, []);
    const focusNode = useCallback((nodeId) => {
        const node = nodes.find((n) => n.id === nodeId);
        if (node && rfInstance) {
            rfInstance.setCenter(node.position.x, node.position.y, {
                zoom: 1.2,
                duration: 400,
            });
        }
    }, [nodes, rfInstance]);
    const handleAutoLayout = useCallback(() => {
        const updates = [];
        // Group nodes by type
        const providers = nodes.filter((n) => n.type === "provider");
        const models = nodes.filter((n) => n.type === "model");
        const roles = nodes.filter((n) => n.type === "role");
        const others = nodes.filter((n) => !["provider", "model", "role"].includes(n.type || ""));
        providers.forEach((node, index) => {
            updates.push({ ...node, position: { x: 40, y: index * 180 + 40 } });
        });
        models.forEach((node, index) => {
            updates.push({ ...node, position: { x: 340, y: index * 120 + 40 } });
        });
        roles.forEach((node, index) => {
            updates.push({ ...node, position: { x: 700, y: index * 180 + 40 } });
        });
        others.forEach((node, index) => {
            updates.push({ ...node, position: { x: 1000, y: index * 180 + 40 } });
        });
        setNodes(updates);
        rfInstance?.fitView({ duration: 800 });
    }, [nodes, rfInstance, setNodes]);
    // Generate menu items based on context
    const getContextMenuItems = useCallback(() => {
        if (!contextMenu)
            return { items: [] };
        if (contextMenu.type === "node") {
            const node = contextMenu.data;
            const items = [];
            let title = "";
            if (node.type === "provider") {
                const data = node.data;
                title = `提供商：${data.label}`;
                items.push({
                    label: "测试连接",
                    icon: Activity,
                    action: () => {
                        devLogger.debug("Test provider", data.providerId);
                    },
                });
                items.push({
                    label: "删除 Provider",
                    icon: Trash2,
                    variant: "danger",
                    action: () => deleteNode(node.id),
                });
            }
            else if (node.type === "model") {
                const data = node.data;
                title = `模型：${data.model}`;
                items.push({
                    label: "删除模型",
                    icon: Trash2,
                    variant: "danger",
                    action: () => deleteNode(node.id),
                });
            }
            else if (node.type === "role") {
                const data = node.data;
                title = `角色：${data.label}`;
                items.push({
                    label: "清除分配",
                    icon: Unplug,
                    variant: "warning",
                    action: () => clearRoleAssignment(data.roleId),
                });
            }
            return { items, title };
        }
        else if (contextMenu.type === "edge") {
            const edge = contextMenu.data;
            const edgeKind = edge.data?.kind;
            // Only model-to-role edges can be deleted (they correspond to a role binding
            // in the config). Provider-to-model edges are structural — deleting the
            // model node (via its context menu) is the correct way to remove a model.
            if (edgeKind === "model-to-role") {
                return {
                    title: "连接操作",
                    items: [
                        {
                            label: "删除连接",
                            icon: Unplug,
                            variant: "danger",
                            action: () => deleteEdge(edge.id),
                        },
                    ],
                };
            }
            // Provider-to-model or unknown edge — show informational items
            const items = [];
            const sourceNode = nodes.find((n) => n.id === edge.source);
            if (sourceNode?.type === "provider") {
                const targetNode = nodes.find((n) => n.id === edge.target);
                if (targetNode?.type === "model") {
                    items.push({
                        label: "删除模型（含连接）",
                        icon: Trash2,
                        variant: "danger",
                        action: () => deleteNode(targetNode.id),
                    });
                }
            }
            return {
                title: edgeKind === "provider-to-model" ? "提供商 → 模型" : "连接操作",
                items: items.length > 0 ? items : [
                    {
                        label: "此连接不可直接删除",
                        icon: Unplug,
                        disabled: true,
                        action: () => { },
                    },
                ],
            };
        }
        return { items: [] };
    }, [clearRoleAssignment, contextMenu, deleteNode, deleteEdge, nodes]);
    const contextMenuView = useMemo(() => getContextMenuItems(), [getContextMenuItems]);
    useEffect(() => {
        if (!providerDraft && providers.length > 0) {
            setProviderDraft(providers[0][0]);
        }
    }, [providerDraft, providers]);
    const handleAddModel = () => {
        const modelName = modelDraft.trim();
        if (!modelName || !providerDraft)
            return;
        addModel(providerDraft, modelName);
        setModelDraft("");
    };
    const handleProviderConcurrencyChange = (providerId, rawValue) => {
        commitConfigUpdate((current) => updateProviderConcurrency(current, providerId, readPositiveInt(rawValue)));
    };
    const handleRoleConcurrencyChange = (roleId, rawValue) => {
        commitConfigUpdate((current) => updateRoleConcurrency(current, roleId, readPositiveInt(rawValue)));
    };
    const handleBindingConcurrencyChange = (roleId, providerId, model, rawValue) => {
        commitConfigUpdate((current) => updateRoleBindingConcurrency(current, roleId, providerId, model, readPositiveInt(rawValue)));
    };
    const isValid = useCallback((connection) => {
        return isValidVisualConnection(connection, nodes);
    }, [nodes]);
    const nodeColor = (node) => {
        if (node.type === "role")
            return "#22d3ee";
        if (node.type === "provider")
            return "#f472b6";
        return "#34d399";
    };
    const handleNodesChange = useCallback((changes) => {
        onNodesChange(changes);
    }, [onNodesChange]);
    if (!config) {
        return (_jsx("div", { className: "rounded-xl border border-white/10 bg-black/30 p-6 text-xs text-text-dim", children: "\u6682\u65E0 LLM \u914D\u7F6E\u6570\u636E\uFF0C\u65E0\u6CD5\u6E32\u67D3\u540F\u90E8\u00B7\u94E8\u9009\u53F8\u3002" }));
    }
    const handleSave = () => {
        if (!onConfigChange)
            return;
        const baseConfig = getCurrentConfig() || config;
        if (!baseConfig)
            return;
        const latestNodes = rfInstance?.getNodes() || nodes;
        const latestEdges = rfInstance?.getEdges() || edges;
        const layout = extractLayoutWithFallback(latestNodes);
        const states = extractNodeStates(latestNodes, latestEdges);
        const finalConfig = {
            ...baseConfig,
            visual_layout: layout,
            visual_node_states: states,
        };
        onConfigChange(finalConfig);
        onSave?.(finalConfig);
    };
    return (_jsxs("div", { "data-testid": "llm-visual-editor", ref: editorContainerRef, className: "relative soft-panel rounded-xl p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3 mb-3", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs font-semibold text-text-main", children: "LLM \u89C6\u89C9\u914D\u7F6E\u7F16\u8F91\u5668 \u00B7 \u540F\u90E8\u00B7\u94E8\u9009\u53F8" }), _jsx("div", { className: "text-[10px] text-text-dim", children: "\u62D6\u62FD\u8FDE\u7EBF\uFF1A\u63D0\u4F9B\u5546 \u2192 \u6A21\u578B \u2192 \u89D2\u8272" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { type: "button", onClick: handleAutoLayout, className: "p-1.5 text-text-dim hover:text-text-main transition-colors", title: "\u81EA\u52A8\u5E03\u5C40", children: _jsx(LayoutGrid, { size: 14 }) }), _jsx("button", { type: "button", onClick: () => rfInstance?.fitView({ duration: 400 }), className: "p-1.5 text-text-dim hover:text-text-main transition-colors", title: "\u9002\u5E94\u89C6\u56FE", children: _jsx(Maximize, { size: 14 }) }), _jsx("div", { className: "w-px h-3 bg-white/10 mx-1" }), _jsx("button", { type: "button", onClick: () => setShowAddModel((prev) => !prev), className: "px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors", children: "\u6DFB\u52A0\u6A21\u578B" }), onSave ? (_jsx("button", { type: "button", "data-testid": "llm-visual-save", onClick: handleSave, className: "px-3 py-1.5 text-[10px] font-semibold bg-emerald-500/[0.15] hover:bg-emerald-500/25 text-emerald-200 rounded transition-colors", children: "\u4FDD\u5B58\u914D\u7F6E" })) : null, validation.issues.length > 0 && (_jsx("button", { type: "button", onClick: () => setShowValidationPanel((v) => !v), className: "ml-2", children: _jsx(ValidationBadge, { count: validation.issues.length }) }))] })] }), showAddModel ? (_jsxs("div", { className: "mb-3 grid grid-cols-1 md:grid-cols-[180px_1fr_auto] gap-2 items-center soft-panel-subtle rounded-lg p-2", children: [_jsxs("select", { className: "soft-inset text-[10px] text-text-main rounded px-2 py-1.5", value: providerDraft, onChange: (event) => setProviderDraft(event.target.value), children: [_jsx("option", { value: "", children: "\u9009\u62E9\u63D0\u4F9B\u5546" }), providers.map(([providerId, provider]) => {
                                const label = typeof provider === "object" &&
                                    provider !== null &&
                                    "name" in provider
                                    ? String(provider.name || providerId)
                                    : providerId;
                                return (_jsx("option", { value: providerId, children: label }, providerId));
                            })] }), _jsx("input", { className: "soft-inset text-[10px] text-text-main rounded px-2 py-1.5", placeholder: "\u6A21\u578B\u540D\u79F0", value: modelDraft, onChange: (event) => setModelDraft(event.target.value) }), _jsx("button", { type: "button", onClick: handleAddModel, className: "px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded", children: "\u6DFB\u52A0" })] })) : null, _jsxs("div", { className: "mb-3 rounded-lg border border-white/10 bg-black/15 px-3 py-2", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(SlidersHorizontal, { size: 14, className: "text-cyan-300" }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "text-[10px] font-semibold text-text-main", children: "\u5E76\u53D1\u5BB9\u91CF" }), _jsx("div", { className: "truncate text-[10px] text-text-dim", children: "Provider \u4E0A\u9650 \u2229 Role \u4E0A\u9650 \u2229 Binding \u4E0A\u9650" })] })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsxs("span", { className: "rounded border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-[10px] text-cyan-100", children: ["Provider ", providers.length] }), _jsxs("span", { className: "rounded border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-100", children: ["Role ", roleConcurrencyRows.length] }), _jsxs("span", { className: "rounded border border-fuchsia-400/20 bg-fuchsia-400/10 px-2 py-1 text-[10px] text-fuchsia-100", children: ["Binding ", bindingRows.length] }), _jsx("button", { type: "button", onClick: () => setShowConcurrencyPanel((value) => !value), className: "inline-flex items-center gap-1 rounded border border-white/10 bg-white/[0.08] px-2.5 py-1 text-[10px] font-semibold text-text-main transition-colors hover:bg-white/[0.14]", "data-testid": "llm-visual-toggle-concurrency", children: showConcurrencyPanel ? (_jsxs(_Fragment, { children: ["\u6536\u8D77", _jsx(ChevronUp, { size: 12 })] })) : (_jsxs(_Fragment, { children: ["\u5C55\u5F00", _jsx(ChevronDown, { size: 12 })] })) })] })] }), showConcurrencyPanel ? (_jsxs("div", { className: "mt-3 grid grid-cols-1 gap-3 border-t border-white/10 pt-3 xl:grid-cols-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "mb-1 text-[10px] font-semibold text-text-dim", children: "Provider" }), _jsx("div", { className: "max-h-28 space-y-1 overflow-y-auto pr-1", children: providers.map(([providerId, provider]) => {
                                            const providerCfg = typeof provider === "object" && provider !== null
                                                ? provider
                                                : {};
                                            const value = typeof providerCfg.max_concurrency === "number"
                                                ? providerCfg.max_concurrency
                                                : "";
                                            return (_jsxs("label", { className: "grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "truncate text-text-main", children: providerId }), _jsx("input", { type: "number", min: "1", step: "1", value: value, onChange: (event) => handleProviderConcurrencyChange(providerId, event.target.value), className: "w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main", placeholder: "auto" })] }, providerId));
                                        }) })] }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "mb-1 text-[10px] font-semibold text-text-dim", children: "Role" }), _jsxs("div", { className: "max-h-28 space-y-1 overflow-y-auto pr-1", children: [roleConcurrencyRows.map(({ roleId, roleCfg }) => {
                                                const value = typeof roleCfg.max_concurrency === "number"
                                                    ? roleCfg.max_concurrency
                                                    : typeof roleCfg.concurrency === "number"
                                                        ? roleCfg.concurrency
                                                        : "";
                                                return (_jsxs("label", { className: "grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim", children: [_jsx("span", { className: "truncate text-text-main", children: getLlmRoleDefinition(roleId).label }), _jsx("input", { type: "number", min: "1", step: "1", value: value, onChange: (event) => handleRoleConcurrencyChange(roleId, event.target.value), className: "w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main", placeholder: "1" })] }, roleId));
                                            }), roleConcurrencyRows.length === 0 ? (_jsx("div", { className: "text-[10px] text-text-dim", children: "\u6682\u65E0 Role \u7ED1\u5B9A" })) : null] })] }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "mb-1 text-[10px] font-semibold text-text-dim", children: "Binding" }), _jsxs("div", { className: "max-h-28 space-y-1 overflow-y-auto pr-1", children: [bindingRows.map(({ roleId, binding, index }) => {
                                                const value = typeof binding.max_concurrency === "number"
                                                    ? binding.max_concurrency
                                                    : typeof binding.concurrency === "number"
                                                        ? binding.concurrency
                                                        : "";
                                                return (_jsxs("label", { className: "grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim", children: [_jsxs("span", { className: "truncate text-text-main", children: [getLlmRoleDefinition(roleId).label, " \u00B7", " ", binding.provider_id, "/", binding.model] }), _jsx("input", { type: "number", min: "1", step: "1", value: value, onChange: (event) => handleBindingConcurrencyChange(roleId, binding.provider_id, binding.model, event.target.value), className: "w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main", placeholder: "auto" })] }, `${roleId}:${binding.provider_id}:${binding.model}:${index}`));
                                            }), bindingRows.length === 0 ? (_jsx("div", { className: "text-[10px] text-text-dim", children: "\u6682\u65E0 Binding" })) : null] })] })] })) : null] }), _jsx("div", { className: "h-[60vh] min-h-[520px] soft-inset rounded-xl overflow-hidden", children: _jsxs(ReactFlow, { nodes: nodes, edges: edges, onNodesChange: handleNodesChange, onNodesDelete: onNodesDelete, onEdgesChange: onEdgesChange, onEdgesDelete: onEdgesDelete, onConnect: onConnect, onNodeContextMenu: onNodeContextMenu, onEdgeContextMenu: onEdgeContextMenu, onPaneContextMenu: onPaneContextMenu, onPaneClick: onPaneClick, nodeTypes: nodeTypes, edgeTypes: edgeTypes, fitView: true, isValidConnection: isValid, className: "bg-transparent", children: [_jsx(MiniMap, { nodeColor: nodeColor, maskColor: "rgba(15,23,42,0.6)", className: "bg-black/70" }), _jsx(Controls, { className: "bg-black/60" }), _jsx(Background, { gap: 24, size: 1, color: "rgba(148,163,184,0.35)" })] }) }), contextMenu && (_jsxs("div", { "data-testid": "llm-visual-context-menu", style: {
                    position: "absolute",
                    top: `${contextMenu.y}px`,
                    left: `${contextMenu.x}px`,
                    zIndex: 99999,
                    minWidth: "180px",
                    background: "#1e293b",
                    border: "1px solid #475569",
                    borderRadius: "8px",
                    padding: "4px",
                    boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
                }, onContextMenu: (e) => e.preventDefault(), children: [contextMenuView.title && (_jsx("div", { style: { borderBottom: "1px solid rgba(255,255,255,0.1)", padding: "8px 12px", fontSize: "12px", fontWeight: 600, color: "#94a3b8" }, children: contextMenuView.title })), _jsx("div", { style: { padding: "4px" }, children: contextMenuView.items.map((item, index) => {
                            const Icon = item.icon;
                            const isDanger = item.variant === "danger";
                            return (_jsxs("button", { type: "button", "data-testid": `llm-visual-context-menu-item-${item.label}`, onClick: () => {
                                    if (!item.disabled) {
                                        item.action();
                                        closeContextMenu();
                                    }
                                }, disabled: item.disabled, style: {
                                    display: "flex",
                                    width: "100%",
                                    alignItems: "center",
                                    gap: "8px",
                                    padding: "6px 8px",
                                    fontSize: "12px",
                                    borderRadius: "4px",
                                    border: "none",
                                    background: "transparent",
                                    cursor: item.disabled ? "not-allowed" : "pointer",
                                    color: item.disabled ? "#475569" : isDanger ? "#f87171" : "#e2e8f0",
                                    opacity: item.disabled ? 0.5 : 1,
                                }, onMouseEnter: (e) => { if (!item.disabled)
                                    e.currentTarget.style.background = isDanger ? "rgba(239,68,68,0.2)" : "rgba(255,255,255,0.1)"; }, onMouseLeave: (e) => { e.currentTarget.style.background = "transparent"; }, children: [Icon && _jsx(Icon, { size: 14 }), _jsx("span", { children: item.label })] }, index));
                        }) })] })), showValidationPanel && validation.issues.length > 0 && (_jsx(ValidationPanel, { issues: validation.issues, onIssueClick: (issue) => focusNode(issue.nodeId), onClose: () => setShowValidationPanel(false) }))] }));
}
