import { FileJson, FileText, MessageSquare, Activity, Folder, ChevronDown, ChevronRight, History } from 'lucide-react';
import { useState, type ComponentType, type SVGProps } from 'react';
import { UI_TERMS } from '@/app/constants/uiTerminology';

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface ArtifactItem {
  id: string;
  name: string;
  icon: IconType;
  path: string;
  badge?: string;
}

interface ArtifactGroup {
  name: string;
  items: ArtifactItem[];
}

const artifactGroups: ArtifactGroup[] = [
  {
    name: '尚书省案卷',
    items: [
      { id: 'pm-tasks', name: 'pm_tasks.contract.json', icon: FileJson, path: 'runtime/contracts/pm_tasks.contract.json', badge: '章奏' },
      { id: 'pm-report', name: 'pm.report.md', icon: FileText, path: 'runtime/results/pm.report.md' },
      { id: 'plan', name: 'plan.md', icon: FileText, path: 'runtime/contracts/plan.md' },
      { id: 'pm-state', name: 'pm.state.json', icon: FileJson, path: 'runtime/state/pm.state.json' },
    ],
  },
  {
    name: '工部案卷',
    items: [
      { id: 'director-result', name: 'director.result.json', icon: FileJson, path: 'runtime/results/director.result.json', badge: '奏报' },
      { id: 'planner', name: 'planner.output.md', icon: FileText, path: 'runtime/results/planner.output.md' },
      { id: 'ollama', name: 'director_llm.output.md', icon: FileText, path: 'runtime/results/director_llm.output.md' },
      { id: 'runlog', name: 'director.runlog.md', icon: FileText, path: 'runtime/logs/director.runlog.md' },
      { id: 'director-subprocess', name: 'director.process.log', icon: FileText, path: 'runtime/logs/director.process.log' },
    ],
  },
  {
    name: '门下封驳',
    items: [
      { id: 'qa', name: 'qa.review.md', icon: FileText, path: 'runtime/results/qa.review.md', badge: '封驳' },
      { id: 'review', name: 'auditor.review.md', icon: FileText, path: 'runtime/results/auditor.review.md' },
      { id: 'gap', name: 'gap_report.md', icon: FileText, path: 'runtime/contracts/gap_report.md' },
    ],
  },
  {
    name: '事件流',
    items: [
      { id: 'dialogue', name: 'dialogue.transcript.jsonl', icon: MessageSquare, path: 'runtime/events/dialogue.transcript.jsonl' },
      { id: 'events', name: 'runtime.events.jsonl', icon: Activity, path: 'runtime/events/runtime.events.jsonl' },
      { id: 'trajectory', name: 'trajectory.json', icon: FileJson, path: 'runtime/trajectory.json' },
    ],
  },
  {
    name: '律令与记忆',
    items: [
      { id: 'policy', name: 'director.policy.json', icon: FileJson, path: 'runtime/policy/director.policy.json' },
      { id: 'memory', name: 'last_state.json', icon: FileJson, path: 'runtime/memory/last_state.json' },
    ],
  },
];

interface ArtifactsSidebarProps {
  onFileSelect: (file: ArtifactItem) => void;
  selectedFileId: string | null;
  onOpenWorkspace?: () => void;
  onOpenHistory?: () => void;
  fileStatusLines?: string[] | null;
}

export function ArtifactsSidebar({ onFileSelect, selectedFileId, onOpenWorkspace, onOpenHistory, fileStatusLines }: ArtifactsSidebarProps) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(artifactGroups.map((g) => g.name))
  );

  const toggleGroup = (groupName: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  };

  return (
    <div className="h-full flex flex-col text-text-main">
      <div className="px-4 py-3 border-b border-white/5">
        <h2 className="text-sm font-heading font-semibold text-text-muted tracking-wide flex items-center gap-2">
          <FileText className="size-4 text-accent" />
          {UI_TERMS.nouns.runtimeArtifacts}
        </h2>
        <p className="text-[10px] text-text-dim font-mono mt-1 opacity-50">runtime/</p>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {artifactGroups.map((group) => {
          const isExpanded = expandedGroups.has(group.name);
          return (
            <div key={group.name} className="border-b border-white/5 last:border-0">
              <button
                onClick={() => toggleGroup(group.name)}
                className="w-full flex items-center gap-2 px-4 py-2 hover:bg-white/5 transition-colors group"
              >
                {isExpanded ? (
                  <ChevronDown className="size-3 text-text-dim group-hover:text-text-main" />
                ) : (
                  <ChevronRight className="size-3 text-text-dim group-hover:text-text-main" />
                )}
                <span className="text-[11px] font-bold text-text-dim uppercase tracking-wider group-hover:text-text-muted transition-colors">
                  {group.name}
                </span>
              </button>

              {isExpanded && (
                <div className="pb-2">
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    const isSelected = selectedFileId === item.id;
                    let badgeText: string | undefined = item.badge;
                    if (Array.isArray(fileStatusLines)) {
                      for (const line of fileStatusLines) {
                        const idx = line.indexOf(':');
                        if (idx > 0) {
                          const label = line.slice(0, idx).trim();
                          const value = line.slice(idx + 1).trim();
                          if (label === item.name) {
                            badgeText = value;
                            break;
                          }
                        }
                      }
                    }
                    return (
                      <button
                        key={item.id}
                        onClick={() => onFileSelect(item)}
                        className={`w-full flex items-center gap-3 px-4 py-1.5 pl-9 transition-all relative ${isSelected
                          ? 'bg-accent/10 text-accent'
                          : 'text-text-muted hover:text-text-main hover:bg-white/5'
                          }`}
                      >
                        {isSelected && (
                          <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-accent shadow-[0_0_8px_currentColor]" />
                        )}
                        <Icon className={`size-4 flex-shrink-0 ${isSelected ? 'text-accent' : 'text-text-dim'}`} />
                        <span className="text-xs truncate flex-1 text-left font-mono">
                          {item.name}
                        </span>
                        {badgeText && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${isSelected
                            ? 'bg-accent/20 text-accent-text border border-accent/20'
                            : 'bg-white/10 text-text-dim'
                            }`}>
                            {badgeText}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 快速链接 */}
      <div className="border-t border-white/5 p-2 space-y-1 bg-black/20">
        <button
          type="button"
          onClick={onOpenWorkspace}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-muted hover:text-accent hover:bg-white/5 rounded transition-colors"
        >
          <Folder className="size-4" />
          <span>打开{UI_TERMS.nouns.workspace}</span>
        </button>
        {onOpenHistory && (
          <button
            type="button"
            onClick={onOpenHistory}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-muted hover:text-accent hover:bg-white/5 rounded transition-colors"
          >
            <History className="size-4" />
            <span>{UI_TERMS.nouns.history}</span>
          </button>
        )}
      </div>
    </div>
  );
}

