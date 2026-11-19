import { useEffect, useRef, useState } from 'react';
import { apiGet } from '@/services/apiClient';
type HistoryKind = 'factory' | 'pm' | 'chief_engineer' | 'director' | 'qa';
type RecordValue = Record<string, unknown>;
const kinds: { id: HistoryKind; label: string }[] = [{ id: 'factory', label: 'Factory 运行' }, { id: 'pm', label: 'PM 会话' }, { id: 'chief_engineer', label: 'CE 会话' }, { id: 'director', label: 'Director 会话' }, { id: 'qa', label: 'QA 会话' }];

/** One-shot historical GETs only. This viewer has no execution controls. */
export function LegacyRunHistory({ workspace, onBackToMain }: { workspace: string; onBackToMain: () => void }) {
  const [kind, setKind] = useState<HistoryKind>('factory');
  const [host, setHost] = useState('electron_workbench');
  const [items, setItems] = useState<RecordValue[]>([]);
  const [detail, setDetail] = useState<RecordValue | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const generation = useRef(0);
  const scoped = (path: string) => `${path}${path.includes('?') ? '&' : '?'}workspace=${encodeURIComponent(workspace)}`;
  useEffect(() => {
    const current = ++generation.current;
    setItems([]); setDetail(null); setError('');
    if (!workspace) { setError('历史记录缺少 workspace 绑定'); return; }
    setLoading(true);
    const query = kind === 'factory' ? '/v2/factory/runs?limit=50' : `/v2/roles/sessions?role=${kind}&host_kind=${encodeURIComponent(host)}&limit=50`;
    void apiGet<RecordValue>(`${query}&workspace=${encodeURIComponent(workspace)}`, '读取历史列表失败').then((result) => {
      if (generation.current !== current) return;
      if (!result.ok || !result.data || result.data.ok === false) throw new Error(result.error || String(result.data?.error || '读取历史列表失败'));
      const records = kind === 'factory' ? result.data.runs : result.data.sessions;
      if (!Array.isArray(records)) throw new Error('历史列表格式不可用');
      setItems(records.filter((item): item is RecordValue => Boolean(item && typeof item === 'object' && (!item.workspace || item.workspace === workspace))));
    }).catch((failure) => { if (generation.current === current) setError(failure instanceof Error ? failure.message : '读取历史列表失败'); }).finally(() => { if (generation.current === current) setLoading(false); });
    return () => { ++generation.current; };
  }, [workspace, kind, host, refreshKey]);
  async function open(item: RecordValue) {
    const current = ++generation.current;
    setError(''); setDetail(null); setLoading(true);
    const id = String(kind === 'factory' ? item.run_id : item.id);
    const base = `${kind === 'factory' ? '/v2/factory/runs' : '/v2/roles/sessions'}/${encodeURIComponent(id)}`;
    try {
      const record = await apiGet<RecordValue>(scoped(base), '读取历史记录失败');
      if (generation.current !== current) return;
      if (!record.ok || !record.data || record.data.ok === false) throw new Error(record.error || '读取历史记录失败');
      const identity = kind === 'factory' ? record.data : record.data.session as RecordValue | undefined;
      if (!identity || identity.workspace !== workspace) throw new Error('历史记录 workspace 不匹配');
      const suffixes = kind === 'factory' ? ['artifacts'] : ['messages', 'artifacts', 'audit'];
      const evidence = await Promise.all(suffixes.map(async (suffix) => {
        const result = await apiGet<RecordValue>(scoped(`${base}/${suffix}`), '读取历史证据失败');
        if (!result.ok || !result.data || result.data.ok === false) throw new Error(result.error || '读取历史证据失败');
        const boundSession = result.data.session as RecordValue | undefined;
        if (boundSession?.workspace && boundSession.workspace !== workspace) throw new Error('历史记录 workspace 不匹配');
        return [suffix, result.data];
      }));
      if (generation.current === current) setDetail({ record: record.data, ...Object.fromEntries(evidence) });
    } catch (failure) { if (generation.current === current) setError(failure instanceof Error ? failure.message : '读取历史记录失败'); }
    finally { if (generation.current === current) setLoading(false); }
  }
  return <section aria-label="历史记录" className="flex h-full min-h-0 flex-col gap-4 overflow-auto p-6">
    <div>
      <p className="cx-eyebrow">READ ONLY</p>
      <h1 className="mt-1 text-xl font-bold tracking-tight">历史记录（只读）</h1>
      <p className="mt-1 text-sm text-[var(--cx-ink-dim)]">旧运行与角色会话保留原始结果；新工程任务请返回工程入口。</p>
    </div>
    <div className="flex flex-wrap items-center gap-2.5">
      <button type="button" onClick={onBackToMain} className="cx-btn cx-btn--ghost">返回工程运行</button>
      <label htmlFor="legacy-history-kind" className="text-sm text-[var(--cx-ink-dim)]">历史类型</label>
      <select id="legacy-history-kind" value={kind} onChange={(event) => setKind(event.target.value as HistoryKind)} className="cx-input cx-input-auto text-sm">{kinds.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      {kind !== 'factory' && <>
        <label htmlFor="legacy-history-host" className="text-sm text-[var(--cx-ink-dim)]">历史宿主</label>
        <select id="legacy-history-host" value={host} onChange={(event) => setHost(event.target.value)} className="cx-input cx-input-auto cx-mono text-xs">{['electron_workbench', 'workflow', 'tui', 'cli', 'api_server', 'headless'].map((value) => <option key={value} value={value}>{value}</option>)}</select>
      </>}
      <button type="button" disabled={loading} onClick={() => setRefreshKey((value) => value + 1)} className="cx-btn cx-btn--ghost disabled:opacity-40">刷新历史</button>
    </div>
    <p className="cx-mono break-all text-xs text-[var(--cx-ink-faint)]">{workspace}</p>
    {loading && <p role="status" className="text-sm text-[var(--cx-ink-dim)]">正在读取历史…</p>}
    {error && <p role="alert" className="rounded-lg border border-[#eeccc9] bg-[var(--cx-red-soft)] px-3 py-2 text-sm text-[var(--cx-red)]">{error}</p>}
    <div className="flex flex-wrap gap-2">{items.map((item) => <button key={String(item.run_id || item.id)} type="button" onClick={() => void open(item)} className="rounded-xl border border-[var(--cx-line)] bg-white px-4 py-3 text-sm shadow-sm transition-colors hover:border-[var(--cx-line-strong)]">{String(item.title || item.run_id || item.id)} · {String(item.status || item.state || 'unknown')}</button>)}</div>
    {!loading && !error && !items.length && <p className="text-sm text-[var(--cx-ink-faint)]">当前工作区暂无此类历史记录</p>}
    {detail && <pre className="whitespace-pre-wrap break-words rounded-xl border border-[rgba(148,170,200,0.16)] bg-[#1a2331] p-4 text-xs leading-relaxed text-[#cfe0d4]">{JSON.stringify(detail, null, 2)}</pre>}
  </section>;
}
