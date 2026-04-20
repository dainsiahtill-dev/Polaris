import { useEffect, useState } from 'react';
import { Terminal } from 'lucide-react';
import { PtyDrawer } from '@/app/components/PtyDrawer';

interface CodexModelBrowserProps {
  providerId: string;
  command?: string;
  tuiArgs?: string[];
  env?: Record<string, string>;
  modelId: string;
  onSelect: (modelId: string) => void;
}

export function CodexModelBrowser({
  providerId,
  command,
  tuiArgs,
  env,
  modelId,
  onSelect,
}: CodexModelBrowserProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(modelId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setDraft(modelId || '');
    setError(null);
  }, [open, modelId]);

  const handleSave = () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError('请输入模型 ID');
      return;
    }
    onSelect(trimmed);
    setOpen(false);
  };

  const isWindows = typeof navigator !== 'undefined' && /windows/i.test(navigator.userAgent);
  const resolvedCommand = command || 'codex';
  const resolvedArgs = tuiArgs || [];
  const buildPsToken = (value: string) => {
    if (!/[\s"]/u.test(value)) return value;
    const escaped = value.replace(/"/g, '`"');
    return `"${escaped}"`;
  };
  const shellLaunch = isWindows ? [buildPsToken(resolvedCommand), ...resolvedArgs.map(buildPsToken)].join(' ') : '';
  const providerConfig = isWindows
    ? {
        id: providerId,
        command: 'powershell.exe',
        env: env || {},
        tui_args: ['-NoLogo', '-NoProfile'],
      use_conpty: false,
      }
    : {
        id: providerId,
        command: resolvedCommand,
        env: env || {},
        tui_args: resolvedArgs,
      use_conpty: false,
      };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="px-3 py-2 text-[10px] font-semibold border border-cyan-400/40 text-cyan-200 rounded hover:border-cyan-400/70 flex items-center gap-1"
      >
        <Terminal className="size-3" />
        模型列表
      </button>
      <PtyDrawer
        open={open}
        onOpenChange={setOpen}
        roleLabel="Codex CLI"
        providerId={providerId}
        providerConfig={providerConfig}
        modelValue={draft}
        onModelChange={(value) => {
          setDraft(value);
          if (error && value.trim()) {
            setError(null);
          }
        }}
        onSaveModel={handleSave}
        onSaveAndTest={handleSave}
        error={error}
        showQuickTest={false}
        bootCommand={isWindows ? shellLaunch : undefined}
        bootCommandDelayMs={isWindows ? 600 : 0}
        bootCommandLabel="Launch Codex"
        autoCommand="/model"
        autoCommandDelayMs={isWindows ? 2200 : 0}
        autoCommandLabel="Send /model"
      />
    </>
  );
}
