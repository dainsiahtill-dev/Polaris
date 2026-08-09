import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * QualityGatePanel - 质量门禁实时可视化
 *
 * 显示门禁状态：PM 质量门、Director 安全门、QA 验收门
 * 颜色编码：GREEN / YELLOW / RED
 */
import { cn } from '@/app/components/ui/utils';
import { useRoles, useSummary, useRuntimeEvents } from '@/runtime';
function evaluateRunLedgerEvidence(projection, waitingDetail = '等待 Run Ledger 证据') {
    if (!projection) {
        return { status: 'pending', detail: waitingDetail };
    }
    if (!projection.available) {
        return {
            status: 'red',
            detail: projection.detail || 'Run Ledger projection 不可用',
        };
    }
    if (projection.total <= 0 && projection.projects.length === 0) {
        return {
            status: 'pending',
            detail: projection.detail || waitingDetail,
        };
    }
    const failedProject = pickFailedLedgerProject(projection.projects);
    if (!projection.ok || projection.failed > 0 || failedProject) {
        return {
            status: 'red',
            detail: failedProject?.detail || projection.detail || 'Run Ledger gate failed',
        };
    }
    return {
        status: 'green',
        detail: `Run Ledger verified ${projection.projected}/${projection.total}`,
    };
}
/**
 * 评估 PM 质量门。PM 的完成率只能表示流程进度；绿色通过必须来自
 * Control Plane Run Ledger，否则会把角色状态冒充为物理证据链。
 */
export function evaluatePMGate(summary, roleState, controlPlaneProjection) {
    let status = 'pending';
    let detail = '';
    if (summary.total === 0) {
        status = 'pending';
        detail = '等待任务分配';
    }
    else if (summary.failed > 0) {
        status = 'red';
        detail = `${summary.failed} 个任务失败`;
    }
    else if (summary.blocked > 0) {
        status = 'yellow';
        detail = `${summary.blocked} 个任务被阻塞`;
    }
    else if (roleState === 'completed') {
        const evidence = evaluateRunLedgerEvidence(controlPlaneProjection, 'PM 已完成，等待 Run Ledger 证据');
        status = evidence.status;
        detail = evidence.detail || '';
    }
    else if (roleState === 'executing' || roleState === 'planning') {
        const completionRate = summary.total > 0 ? (summary.completed / summary.total) * 100 : 0;
        if (completionRate >= 50) {
            status = 'yellow';
            detail = `进行中 - ${Math.round(completionRate)}%，等待 Run Ledger`;
        }
        else {
            status = 'yellow';
            detail = '规划/执行中';
        }
    }
    else {
        status = 'pending';
        detail = '等待启动';
    }
    return {
        id: 'pm-gate',
        name: 'PM 质量门',
        status,
        description: '任务规划与执行质量',
        detail,
        lastCheck: new Date().toISOString(),
    };
}
/**
 * 评估 Director 安全门。工具越权和错误仍可直接置红；执行完成不能直接
 * 置绿，必须等待 Run Ledger 的 write receipt / gate evidence。
 */
export function evaluateDirectorGate(roleState, events, controlPlaneProjection) {
    const recentErrors = events.filter(e => e.severity === 'error' &&
        Date.now() - new Date(e.ts).getTime() < 30 * 60 * 1000);
    const unauthorizedEvents = events.filter(e => e.detail?.includes('unauthorized') || e.message.includes('unauthorized'));
    let status = 'pending';
    let detail = '';
    if (roleState === 'failed') {
        status = 'red';
        detail = 'Director 执行失败';
    }
    else if (unauthorizedEvents.length > 0) {
        status = 'red';
        detail = `${unauthorizedEvents.length} 次越权事件`;
    }
    else if (recentErrors.length > 3) {
        status = 'red';
        detail = `${recentErrors.length} 个错误事件`;
    }
    else if (recentErrors.length > 0) {
        status = 'yellow';
        detail = `${recentErrors.length} 个警告事件`;
    }
    else if (roleState === 'completed') {
        const evidence = evaluateRunLedgerEvidence(controlPlaneProjection, 'Director 已完成，等待 Run Ledger 证据');
        status = evidence.status;
        detail = evidence.detail || '';
    }
    else if (roleState === 'executing') {
        status = 'yellow';
        detail = '执行中，等待 Run Ledger';
    }
    else if (roleState === 'idle') {
        status = 'pending';
        detail = '等待执行';
    }
    else {
        status = 'pending';
        detail = roleState;
    }
    return {
        id: 'director-gate',
        name: 'Director 安全门',
        status,
        description: '代码执行与工具调用安全',
        detail,
        lastCheck: new Date().toISOString(),
    };
}
function pickFailedLedgerProject(projects) {
    return projects.find((project) => !project.ok || project.failed_gate_count > 0) ?? null;
}
function evaluateRunLedgerQAGate(projection) {
    const evidence = evaluateRunLedgerEvidence(projection);
    return {
        id: 'qa-gate',
        name: 'QA 验收门',
        status: evidence.status,
        description: '质量验收与测试通过',
        detail: evidence.detail,
        lastCheck: new Date().toISOString(),
    };
}
/**
 * 评估 QA 验收门。QA 成功必须来自平台 Run Ledger；角色状态和 summary
 * 只能表达流程进度，不能替代物理证据链。
 */
export function evaluateQAGate(roleState, summary, controlPlaneProjection) {
    if (controlPlaneProjection) {
        return evaluateRunLedgerQAGate(controlPlaneProjection);
    }
    let status = 'pending';
    let detail = '';
    if (summary.total === 0) {
        status = 'pending';
        detail = '等待 Run Ledger 证据';
    }
    else if (summary.failed > 0) {
        status = 'red';
        detail = `${summary.failed} 个任务未通过`;
    }
    else if (roleState === 'completed') {
        status = 'pending';
        detail = '角色已完成，等待 Run Ledger 证据';
    }
    else if (roleState === 'executing' || roleState === 'verification') {
        status = 'yellow';
        detail = '验收中';
    }
    else {
        status = 'pending';
        detail = '等待验收';
    }
    return {
        id: 'qa-gate',
        name: 'QA 验收门',
        status,
        description: '质量验收与测试通过',
        detail,
        lastCheck: new Date().toISOString(),
    };
}
function GateItem({ gate }) {
    const statusStyles = {
        green: {
            border: 'border-emerald-500/30',
            bg: 'bg-emerald-500/5',
            icon: 'text-emerald-400',
            badge: 'bg-emerald-500/20 text-emerald-300',
            glow: '',
        },
        yellow: {
            border: 'border-amber-500/30',
            bg: 'bg-amber-500/5',
            icon: 'text-amber-400',
            badge: 'bg-amber-500/20 text-amber-300',
            glow: '',
        },
        red: {
            border: 'border-red-500/30',
            bg: 'bg-red-500/5',
            icon: 'text-red-400',
            badge: 'bg-red-500/20 text-red-300',
            glow: '',
        },
        pending: {
            border: 'border-slate-600/30',
            bg: 'bg-slate-800/30',
            icon: 'text-slate-400',
            badge: 'bg-slate-500/20 text-slate-300',
            glow: '',
        },
    };
    const style = statusStyles[gate.status];
    return (_jsxs("div", { className: cn('rounded-xl border p-4', style.border, style.bg), children: [_jsxs("div", { className: "flex items-start justify-between mb-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: cn('rounded-lg p-1.5', style.icon), children: [gate.status === 'green' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" }) })), gate.status === 'yellow' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" }) })), gate.status === 'red' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" }) })), gate.status === 'pending' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" }) }))] }), _jsxs("div", { children: [_jsx("div", { className: "text-sm font-medium text-slate-200", children: gate.name }), _jsx("div", { className: "text-[10px] text-slate-500", children: gate.description })] })] }), _jsx("span", { className: cn('text-[10px] px-2 py-0.5 rounded', style.badge), children: gate.status === 'green' ? '通过' : gate.status === 'yellow' ? '警告' : gate.status === 'red' ? '失败' : '待检' })] }), gate.detail && (_jsx("div", { className: "text-xs text-slate-400 mt-2 pl-8", children: gate.detail }))] }));
}
export function QualityGatePanel({ controlPlaneProjection } = {}) {
    const roles = useRoles();
    const summary = useSummary();
    const events = useRuntimeEvents();
    // 评估各门禁状态
    const pmGate = evaluatePMGate(summary, roles.PM.state, controlPlaneProjection);
    const directorGate = evaluateDirectorGate(roles.Director.state, events, controlPlaneProjection);
    const qaGate = evaluateQAGate(roles.QA.state, summary, controlPlaneProjection);
    const gates = [pmGate, directorGate, qaGate];
    // 总体状态
    const passedGates = gates.filter(g => g.status === 'green').length;
    const failedGates = gates.filter(g => g.status === 'red').length;
    const warningGates = gates.filter(g => g.status === 'yellow').length;
    const overallStatus = failedGates > 0 ? 'red' :
        warningGates > 0 ? 'yellow' :
            passedGates === gates.length ? 'green' :
                'pending';
    return (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: "h-4 w-0.5 rounded-full soft-divider" }), _jsx("h2", { className: "text-xs font-bold tracking-[0.2em] text-slate-300/80", children: "\u8D28 \u91CF \u95E8 \u7981" }), _jsxs("div", { className: "flex items-center gap-2 ml-auto", children: [_jsxs("div", { className: "flex items-center gap-1 text-xs", children: [_jsx("span", { className: "h-2 w-2 rounded-full bg-emerald-400" }), _jsx("span", { className: "text-emerald-400", children: passedGates })] }), _jsxs("div", { className: "flex items-center gap-1 text-xs", children: [_jsx("span", { className: "h-2 w-2 rounded-full bg-amber-400" }), _jsx("span", { className: "text-amber-400", children: warningGates })] }), _jsxs("div", { className: "flex items-center gap-1 text-xs", children: [_jsx("span", { className: "h-2 w-2 rounded-full bg-red-400" }), _jsx("span", { className: "text-red-400", children: failedGates })] })] })] }), _jsx("div", { className: "space-y-2", children: gates.map(gate => (_jsx(GateItem, { gate: gate }, gate.id))) })] }));
}
