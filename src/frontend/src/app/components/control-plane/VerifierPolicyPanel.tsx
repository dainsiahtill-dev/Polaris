import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Eye, Globe2, Loader2, Save, ShieldCheck, TerminalSquare, TriangleAlert } from 'lucide-react';

import {
  getVerifierPolicy,
  updateVerifierPolicy,
  type VerifierPolicy,
  type VerifierPolicyScript,
} from '@/services/controlPlane';

const CAPABILITIES = [
  { key: 'browser', label: 'Browser 验收', icon: Globe2 },
  { key: 'visual', label: '视觉验收', icon: Eye },
  { key: 'llm_judge', label: '多模态 QA', icon: ShieldCheck },
  { key: 'custom_script', label: '用户脚本', icon: TerminalSquare },
] as const;

type CapabilityKey = (typeof CAPABILITIES)[number]['key'];

interface DraftState {
  enabled: Record<CapabilityKey, boolean>;
  required: Record<CapabilityKey, boolean>;
  customScripts: VerifierPolicyScript[];
}

function buildDraft(policy: VerifierPolicy | null): DraftState {
  return {
    enabled: {
      browser: Boolean(policy?.capabilities.browser.enabled),
      visual: Boolean(policy?.capabilities.visual.enabled),
      llm_judge: Boolean(policy?.capabilities.llm_judge.enabled),
      custom_script: Boolean(policy?.capabilities.custom_script.enabled),
    },
    required: {
      browser: Boolean(policy?.capabilities.browser.required),
      visual: Boolean(policy?.capabilities.visual.required),
      llm_judge: Boolean(policy?.capabilities.llm_judge.required),
      custom_script: Boolean(policy?.capabilities.custom_script.required),
    },
    customScripts: policy?.custom_scripts ?? [],
  };
}

function requiredModalities(draft: DraftState): string[] {
  return CAPABILITIES
    .filter((item) => draft.enabled[item.key] && draft.required[item.key])
    .map((item) => item.key);
}

function scriptId(path: string): string {
  const leaf = path.split(/[\\/]/).pop() || 'custom-script';
  return leaf.replace(/\.[^.]+$/, '') || 'custom-script';
}

export function VerifierPolicyPanel() {
  const [policy, setPolicy] = useState<VerifierPolicy | null>(null);
  const [draft, setDraft] = useState<DraftState>(() => buildDraft(null));
  const [scriptPath, setScriptPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPolicy = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await getVerifierPolicy();
    if (result.ok && result.data) {
      setPolicy(result.data);
      setDraft(buildDraft(result.data));
    } else {
      setError(result.error || '读取验收策略失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadPolicy();
  }, [loadPolicy]);

  const dirty = useMemo(() => {
    if (!policy) return false;
    const current = buildDraft(policy);
    return JSON.stringify(current) !== JSON.stringify(draft);
  }, [draft, policy]);

  const toggleEnabled = (key: CapabilityKey, value: boolean) => {
    setDraft((prev) => ({
      ...prev,
      enabled: { ...prev.enabled, [key]: value },
      required: { ...prev.required, [key]: value ? prev.required[key] : false },
    }));
  };

  const toggleRequired = (key: CapabilityKey, value: boolean) => {
    setDraft((prev) => ({
      ...prev,
      required: { ...prev.required, [key]: value },
    }));
  };

  const addScript = () => {
    const normalized = scriptPath.trim().replace(/\\/g, '/').replace(/^\.?\//, '');
    if (!normalized) return;
    setDraft((prev) => ({
      ...prev,
      enabled: { ...prev.enabled, custom_script: true },
      customScripts: [
        ...prev.customScripts,
        {
          id: scriptId(normalized),
          path: normalized,
          modality: 'custom_script',
          enabled: true,
          required: false,
        },
      ],
    }));
    setScriptPath('');
  };

  const removeScript = (index: number) => {
    setDraft((prev) => ({
      ...prev,
      customScripts: prev.customScripts.filter((_, itemIndex) => itemIndex !== index),
    }));
  };

  const savePolicy = async () => {
    setSaving(true);
    setError(null);
    const result = await updateVerifierPolicy({
      browser_enabled: draft.enabled.browser,
      visual_enabled: draft.enabled.visual,
      llm_judge_enabled: draft.enabled.llm_judge,
      custom_script_enabled: draft.enabled.custom_script,
      required_modalities: requiredModalities(draft),
      custom_scripts: draft.customScripts,
    });
    if (result.ok && result.data) {
      setPolicy(result.data);
      setDraft(buildDraft(result.data));
    } else {
      setError(result.error || '保存验收策略失败');
    }
    setSaving(false);
  };

  return (
    <section className="soft-panel-subtle rounded-xl border border-cyan-400/15 p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-text-main">
            <ShieldCheck className="size-4 text-cyan-300" />
            平台验收策略
          </div>
          <p className="mt-1 text-[11px] leading-5 text-text-dim">
            Browser、视觉、多模态 QA 和用户脚本都是可选验收能力。未启用时不会阻塞正式项目，也不会由内部测试设施决定。
          </p>
        </div>
        <button
          type="button"
          onClick={savePolicy}
          disabled={saving || loading || !dirty}
          className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-[11px] font-semibold text-cyan-100 transition hover:bg-cyan-400/15 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
          保存策略
        </button>
      </div>

      {error ? (
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-[11px] text-red-100">
          <TriangleAlert className="mt-0.5 size-3 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-2">
        {CAPABILITIES.map(({ key, label, icon: Icon }) => {
          const status = policy?.capabilities[key];
          const canRequire = draft.enabled[key] && Boolean(status?.available || draft.required[key]);
          return (
            <div key={key} className="rounded-lg border border-white/10 bg-black/20 p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Icon className="size-4 text-cyan-200" />
                  <div>
                    <div className="text-xs font-semibold text-text-main">{label}</div>
                    <div className="mt-0.5 flex items-center gap-1 text-[10px] text-text-dim">
                      {status?.available ? (
                        <CheckCircle2 className="size-3 text-emerald-300" />
                      ) : (
                        <TriangleAlert className="size-3 text-amber-300" />
                      )}
                      <span>{status?.available ? '环境可用' : '环境未声明'}</span>
                    </div>
                  </div>
                </div>
                <label className="flex items-center gap-2 text-[11px] text-text-muted">
                  <input
                    type="checkbox"
                    checked={draft.enabled[key]}
                    onChange={(event) => toggleEnabled(key, event.target.checked)}
                    className="size-4 rounded border-white/20 bg-black/30 text-cyan-300 focus:ring-cyan-300/40"
                  />
                  启用
                </label>
              </div>
              <label className="mt-3 flex items-center gap-2 text-[11px] text-text-muted">
                <input
                  type="checkbox"
                  checked={draft.required[key]}
                  disabled={!canRequire}
                  onChange={(event) => toggleRequired(key, event.target.checked)}
                  className="size-4 rounded border-white/20 bg-black/30 text-cyan-300 focus:ring-cyan-300/40 disabled:opacity-40"
                />
                设为必需证据
              </label>
              {draft.enabled[key] && !status?.available ? (
                <p className="mt-2 text-[10px] leading-4 text-amber-100/75">
                  当前环境未声明该能力；可以保留启用意图，但不能新增为必需证据。
                </p>
              ) : null}
              {!status?.available && status?.reason ? (
                <p className="mt-2 text-[10px] leading-4 text-amber-100/75">{status.reason}</p>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3">
        <div className="mb-2 text-xs font-semibold text-text-main">自定义脚本</div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={scriptPath}
            onChange={(event) => setScriptPath(event.target.value)}
            placeholder="tests/physics_verifier.py"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-xs text-text-main outline-none focus:border-cyan-300/60"
          />
          <button
            type="button"
            onClick={addScript}
            className="rounded-lg border border-white/10 px-3 py-2 text-[11px] text-text-muted transition hover:border-cyan-300/40 hover:text-cyan-100"
          >
            添加
          </button>
        </div>
        {draft.customScripts.length > 0 ? (
          <div className="mt-3 space-y-2">
            {draft.customScripts.map((script, index) => (
              <div key={`${script.path}-${index}`} className="flex items-center justify-between gap-3 rounded-md bg-white/[0.04] px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-[11px] font-medium text-text-main">{script.path}</div>
                  <div className="text-[10px] text-text-dim">{script.modality}</div>
                </div>
                <button
                  type="button"
                  onClick={() => removeScript(index)}
                  className="shrink-0 text-[10px] text-text-dim hover:text-red-200"
                >
                  移除
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
