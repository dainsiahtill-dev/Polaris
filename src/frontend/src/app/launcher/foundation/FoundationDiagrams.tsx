import { useId, useState } from 'react';
import { Layers3, FileText, BrainCircuit, ShieldCheck, Terminal, GitCommitHorizontal, Database, CheckCheck, ScanText } from 'lucide-react';
import type { FoundationDiagramKind } from './schema';

type Node = { id: string; title: string; owner: string; detail: string; evidence: string; icon: typeof Layers3 };
const diagrams: Record<Exclude<FoundationDiagramKind, 'none'>, { title: string; nodes: Node[] }> = {
  architecture: { title: '分层调用与能力归属', nodes: [
    { id: 'delivery', title: 'Delivery', owner: 'HTTP / CLI / WebSocket', detail: '接收请求、绑定 workspace、展示平台投影。传输层不决定任务是否通过。', evidence: 'polaris/delivery；runtime.v2', icon: Terminal },
    { id: 'application', title: 'Application', owner: '用例编排', detail: '组合可复用 Cell 能力，推进用例。避免创建第二套执行状态或工具权限。', evidence: 'polaris/application；Cell public contracts', icon: Layers3 },
    { id: 'cell', title: 'Cells', owner: '独立能力 owner', detail: '角色、任务、运行账本各持有自己的领域状态；跨 Cell 经 public 契约调用，不越过私有实现。', evidence: 'polaris/cells；public / internal', icon: ShieldCheck },
    { id: 'kernelone', title: 'KernelOne', owner: '通用 OS 原语', detail: '提供文件、事件存储、锁、模型与工具适配等底座。TransactionKernel 实际属于 roles.kernel Cell。', evidence: 'polaris/kernelone；roles/kernel/internal/transaction_kernel.py:21', icon: Database },
  ] },
  transaction: { title: '单轮执行与独立提交屏障', nodes: [
    { id: 'context', title: 'Context', owner: '角色内核', detail: '绑定稳定 run/task/attempt，组装角色上下文和工具定义。身份定位不等于写权限。', evidence: 'transaction_turn_executor.py:75；transaction_turn_id.py:311', icon: FileText },
    { id: 'decision', title: 'Decision', owner: 'LLM + 确定性解码', detail: '模型提出回答或工具调用；代码归一化和验证候选决策。不会按模型文字直接授予副作用权限。', evidence: 'transaction/decision_pipeline.py:299', icon: BrainCircuit },
    { id: 'batch', title: 'ToolBatch', owner: '确定性执行计划', detail: '按显式效果分组：并发只读、串行读、串行写与异步收据。准备完整 mutation inventory。', evidence: 'tool_batch_runtime.py:1021；_executor_execute.py:800', icon: Layers3 },
    { id: 'deo', title: 'DEO claim', owner: 'TaskRuntime / roles.kernel', detail: 'TaskRuntime 持久 claim 与 CAS 绑定原 owner；roles.kernel 维护 PID 和原对象身份绑定的一次 fence。写入的 shadow 只能只读准备。', evidence: 'directed_effect_lifecycle.py:946', icon: ShieldCheck },
    { id: 'effect', title: 'Effect receipt', owner: '物理工具 / TaskRuntime', detail: '记录真实效果及其哈希绑定。工具执行成功不等于业务需求被满足；未知效果需恢复，不盲重放。', evidence: 'directed_effect_mutation_port.py:881', icon: Terminal },
    { id: 'complete', title: 'COMPLETED', owner: '单轮内存状态', detail: '工具结果收口，单轮状态机已结束。此时整轮结果可能尚未持久提交，不代表项目完成。', evidence: 'turn_state_machine.py:62；turn_transaction_controller.py:806', icon: CheckCheck },
    { id: 'commit', title: 'OutcomeCommit', owner: 'roles.kernel / FactStream 存储', detail: 'COMPLETED 后的独立 durable commit 屏障。落盘成功才产生 CommitReceipt / SealedTurn；它不等于项目验收。', evidence: 'turn_transaction_controller.py:815；outcome_commit.py:55', icon: GitCommitHorizontal },
    { id: 'settlement', title: 'Settlement', owner: 'Factory / TaskRuntime / Run Ledger', detail: 'TaskRuntime 和 Factory 闭合执行与资源义务；Run Ledger 投影 closed / release_allowed。资源可释放不代表 ProjectOutcome 接受，也不替代独立 QA。', evidence: 'run_ledger/public/settlement_barrier.py:76', icon: Database },
  ] },
  context: { title: '从源事实到最终请求', nodes: [
    { id: 'facts', title: '源事实', owner: '合同 / 蓝图 / 当前文件', detail: '事实由原 owner 持有。ContextOS 读取并投影，不重新授予工具权限或写入验收结论。', evidence: 'ContextOS；PM Contract；CE Blueprint', icon: Database },
    { id: 'slots', title: '上下文槽位', owner: '角色视角装配', detail: '把当前任务、失败反馈和源证据映射到明确槽位；摘要不能替代完整必需证据。', evidence: 'context slots；role context gateway', icon: Layers3 },
    { id: 'budget', title: '预算与压缩', owner: '装配策略', detail: '控制上下文和输出容量，保留任务必需信息。容量利用率不等于质量覆盖度。', evidence: 'request_preparer；model window', icon: ScanText },
    { id: 'body', title: '最终 provider body', owner: 'Provider adapter', detail: '消息、tools、tool_choice 与 response_format 组成规范化请求。只有受治理且实际绑定的传输路径能证明 wire body；普通快照可能仅是规范投影。', evidence: 'final physical provider request', icon: FileText },
    { id: 'audit', title: '证据审计', owner: '最终请求审计', detail: '在已绑定的最终请求检查角色、工具、token、required refs 与 source hashes。metadata 有值不证明 body 已包含；规范投影审计不等于 wire 资格。', evidence: 'final_request_context_audit；evidence coverage', icon: ShieldCheck },
    { id: 'model', title: '模型调用', owner: 'Transport / Provider', detail: '准备请求通过不等于真实传输已发生，收到模型结果也不等于工具执行或项目交付。', evidence: 'provider attempt；response correlation', icon: BrainCircuit },
  ] },
  delivery: { title: '项目交付与独立验收', nodes: [
    { id: 'pm', title: 'PM', owner: '任务合同', detail: '定义目标、约束与可验收任务。PM 输出需交给 Chief Engineer，不能直达 Director。', evidence: 'PM → Chief Engineer → Director', icon: FileText },
    { id: 'ce', title: 'Chief Engineer', owner: '蓝图与交接', detail: '将任务转成实现接口、源范围及验证义务；真实交接能力约束后续执行。', evidence: 'CE Blueprint；handoff decision；execution envelope', icon: Layers3 },
    { id: 'director', title: 'Director', owner: '原任务执行会话', detail: '连续读、修改、执行验证，消费当前失败反馈。副作用必须经实际授权工具和收据链。', evidence: 'TaskRuntime；TransactionKernel；Effect receipt', icon: Terminal },
    { id: 'verifier', title: 'Verifier / QA', owner: '当前产物验收', detail: '真实执行已声明命令；需要语义 QA 时依据原需求独立评审。exit 0 或自生成测试不能替代全部验收。', evidence: 'current input / argv / cwd；independent verdict', icon: CheckCheck },
    { id: 'ledger', title: 'Run Ledger', owner: '事实投影', detail: '区分证据缺失与证据真实失败，绑定执行和验收结果。历史计数不应伪装为当前进展。', evidence: 'missing_required / failed_required modalities', icon: Database },
    { id: 'close', title: '结算与接受', owner: 'ProjectOutcome · 多 owner 证据绑定', detail: '资源结算允许释放；ProjectOutcome 绑定合同、当前验证和 QA 等各 owner 证据计算项目接受。它不把 cleanup barrier.passed 当成最终接受事实。', evidence: 'Resource closure；ProjectOutcome；current verdicts', icon: ShieldCheck },
  ] },
};

export function FoundationDiagrams({ kind, staticMode = false }: { kind: FoundationDiagramKind; staticMode?: boolean }) {
  const [selected, setSelected] = useState('');
  const marker = `foundation-arrow-${useId().replace(/:/g, '')}`;
  if (kind === 'none') return null;
  const diagram = diagrams[kind];
  const active = diagram.nodes.find(node => node.id === selected) ?? diagram.nodes[0];
  return <section className="foundation-diagram" aria-label={diagram.title}>
    <div className="foundation-diagram-heading"><h3>{diagram.title}</h3><span>{staticMode ? '静态原理示意 · 非运行测量' : '静态原理示意 · 点击节点查看边界'}</span></div>
    <div className="foundation-diagram-flow" role="group" aria-label="机制节点">
      {diagram.nodes.map((node, index) => <div className="foundation-diagram-step" key={node.id}>
        {staticMode ? <div className="foundation-node"><node.icon aria-hidden="true" size={20} /><strong>{node.title}</strong><span>{node.owner}</span></div> : <button type="button" className="foundation-node" aria-pressed={active.id === node.id} onClick={() => setSelected(node.id)}>
          <node.icon aria-hidden="true" size={20} /><strong>{node.title}</strong><span>{node.owner}</span>
        </button>}
        {index < diagram.nodes.length - 1 && <svg className="foundation-flow-arrow" viewBox="0 0 36 24" aria-hidden="true"><defs><marker id={`${marker}-${index}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs><path d="M 2 12 H 32" fill="none" stroke="currentColor" strokeWidth="1.5" markerEnd={`url(#${marker}-${index})`} /></svg>}
      </div>)}
    </div>
    {!staticMode && <div className="foundation-node-detail" role="region" aria-label="节点边界说明" aria-live="polite">
      <div><strong>{active.title}</strong><p>{active.detail}</p></div><code>{active.evidence}</code>
    </div>}
  </section>;
}
