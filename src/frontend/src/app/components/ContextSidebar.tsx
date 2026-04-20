import { useState } from 'react';
import { MessageSquare, FileText, Brain, Database, Camera, Bot } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { DialoguePanel, DialogueEvent } from '@/app/components/DialoguePanel';
import { MemoPanel, MemoItem } from '@/app/components/MemoPanel';
import { MemoryPanel } from '@/app/components/MemoryPanel';
import { CognitionPanel } from '@/app/components/CognitionPanel';
import { SnapshotPanel } from '@/app/components/SnapshotPanel';
import type { ResidentStatusPayload } from '@/app/types/appContracts';

export type ContextTab = 'dialogue' | 'memos' | 'memory' | 'snapshot' | 'agi';

interface ContextSidebarProps {
    // Dialogue Props
    dialogueEvents: DialogueEvent[];
    live: boolean;
    dialogueLoading: boolean;
    onClearDialogueLogs?: () => void | Promise<void>;
    clearingDialogueLogs?: boolean;

    // Memo Props
    memoItems: MemoItem[];
    memoSelected: MemoItem | null;
    memoContent: string;
    memoMtime: string;
    memoLoading: boolean;
    memoError: string | null;
    onSelectMemo: (item: MemoItem) => void;

    // Memory Props
    memoryContent: string;
    memoryMtime: string;
    memoryLoading: boolean;
    memoryError: string | null;
    showCognition: boolean;
    setShowCognition: (show: boolean) => void;
    settingsShowMemory: boolean;
    anthroState?: AnthroState | null;

    // Snapshot Props
    snapshotTimestamp?: string | null;
    snapshotFileStatus?: string[] | null;
    snapshotFilePaths?: string[] | null;
    snapshotDirectorState?: Record<string, unknown> | null;
    resident?: ResidentStatusPayload | null;
}

interface AnthroState {
    last_reflection_step: number;
    recent_error_count: number;
    total_memories: number;
    total_reflections: number;
}

export function ContextSidebar({
    dialogueEvents,
    live,
    dialogueLoading,
    onClearDialogueLogs,
    clearingDialogueLogs = false,
    memoItems,
    memoSelected,
    memoContent,
    memoMtime,
    memoLoading,
    memoError,
    onSelectMemo,
    memoryContent,
    memoryMtime,
    memoryLoading,
    memoryError,
    showCognition,
    setShowCognition,
    settingsShowMemory,
    anthroState,
    snapshotTimestamp,
    snapshotFileStatus,
    snapshotFilePaths,
    snapshotDirectorState,
    resident,
}: ContextSidebarProps) {
    const [activeTab, setActiveTab] = useState<ContextTab>('dialogue');

    return (
        <div className="flex h-full glass-bubble border-l-0 overflow-hidden">
            {/* Tab Strip (Vertical Left) */}
            <div className="w-14 flex flex-col items-center py-6 gap-6 border-r border-white/5 bg-black/40 backdrop-blur-xl z-20">
                <TabButton
                    active={activeTab === 'dialogue'}
                    onClick={() => setActiveTab('dialogue')}
                    icon={<MessageSquare className="size-5" />}
                    label="廷议"
                />
                <TabButton
                    active={activeTab === 'memos'}
                    onClick={() => setActiveTab('memos')}
                    icon={<FileText className="size-5" />}
                    label="备忘"
                />
                {settingsShowMemory && (
                    <TabButton
                        active={activeTab === 'memory'}
                        onClick={() => setActiveTab('memory')}
                        icon={showCognition ? <Brain className="size-5" /> : <Database className="size-5" />}
                        label="忆库"
                    />
                )}
                <TabButton
                    active={activeTab === 'snapshot'}
                    onClick={() => setActiveTab('snapshot')}
                    icon={<Camera className="size-5" />}
                    label="快照"
                />
                <TabButton
                    active={activeTab === 'agi'}
                    onClick={() => setActiveTab('agi')}
                    icon={<Bot className="size-5" />}
                    label="AGI"
                />
            </div>

            {/* Content Area */}
            <div className="flex-1 min-w-0 flex flex-col relative bg-gradient-to-br from-transparent to-black/20">
                <AnimatePresence mode="wait">
                    {activeTab === 'dialogue' && (
                        <motion.div
                            key="dialogue"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            transition={{ duration: 0.2, ease: "easeOut" }}
                            className="absolute inset-0 flex flex-col"
                        >
                            <div className="flex-none p-3 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md">
                                <div className="flex items-center gap-2">
                                    <MessageSquare className="size-4 text-blue-400" />
                                    <span className="text-xs font-bold text-text-main uppercase tracking-widest">廷议录</span>
                                </div>
                                <div className="text-[10px] text-text-dim px-2 py-0.5 rounded-full bg-black/30 border border-white/5">
                                    {live ? '在值' : '离线'}
                                </div>
                            </div>
                            <div className="flex-1 min-h-0 relative">
                                <DialoguePanel
                                    events={dialogueEvents}
                                    live={live}
                                    loading={dialogueLoading}
                                    onClearLogs={onClearDialogueLogs}
                                    clearingLogs={clearingDialogueLogs}
                                />
                            </div>
                        </motion.div>
                    )}

                    {activeTab === 'memos' && (
                        <motion.div
                            key="memos"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            transition={{ duration: 0.2, ease: "easeOut" }}
                            className="absolute inset-0 flex flex-col"
                        >
                            <MemoPanel
                                items={memoItems}
                                selected={memoSelected}
                                content={memoContent}
                                mtime={memoMtime}
                                loading={memoLoading}
                                error={memoError}
                                onSelect={onSelectMemo}
                            />
                        </motion.div>
                    )}

                    {activeTab === 'memory' && settingsShowMemory && (
                        <motion.div
                            key="memory"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            transition={{ duration: 0.2, ease: "easeOut" }}
                            className="absolute inset-0 flex flex-col"
                        >
                            <div className="flex-none p-2 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md">
                                <div className="flex items-center gap-2">
                                    {showCognition ? <Brain className="size-4 text-purple-400" /> : <Database className="size-4 text-blue-400" />}
                                    <span className="text-xs font-bold text-text-main uppercase tracking-widest">忆库</span>
                                </div>
                                <div className="flex bg-black/30 p-0.5 rounded-lg border border-white/5">
                                    <button
                                        onClick={() => setShowCognition(true)}
                                        className={`px-2 py-1 text-[10px] rounded transition-all ${showCognition ? 'bg-purple-500/20 text-purple-300' : 'text-gray-500 hover:text-gray-300'}`}
                                    >
                                        心镜
                                    </button>
                                    <button
                                        onClick={() => setShowCognition(false)}
                                        className={`px-2 py-1 text-[10px] rounded transition-all ${!showCognition ? 'bg-blue-500/20 text-blue-300' : 'text-gray-500 hover:text-gray-300'}`}
                                    >
                                        原文
                                    </button>
                                </div>
                            </div>
                            <div className="flex-1 min-h-0 relative overflow-hidden">
                                {showCognition ? (
                                    <CognitionPanel events={dialogueEvents} loading={!live} anthroState={anthroState} />
                                ) : (
                                    <MemoryPanel
                                        content={memoryContent}
                                        mtime={memoryMtime}
                                        loading={memoryLoading}
                                        error={memoryError}
                                    />
                                )}
                            </div>
                        </motion.div>
                    )}

                    {activeTab === 'snapshot' && (
                        <motion.div
                            key="snapshot"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            transition={{ duration: 0.2, ease: "easeOut" }}
                            className="absolute inset-0 flex flex-col"
                        >
                            <div className="flex-none p-3 border-b border-white/5 flex items-center bg-white/5 backdrop-blur-md">
                                <div className="flex items-center gap-2">
                                    <Camera className="size-4 text-green-400" />
                                    <span className="text-xs font-bold text-text-main uppercase tracking-widest">朝堂快照</span>
                                </div>
                            </div>
                            <div className="flex-1 min-h-0 relative overflow-auto">
                                <SnapshotPanel
                                    timestamp={snapshotTimestamp}
                                    fileStatus={snapshotFileStatus}
                                    filePaths={snapshotFilePaths}
                                    directorState={snapshotDirectorState}
                                />
                            </div>
                        </motion.div>
                    )}

                    {activeTab === 'agi' && (
                        <motion.div
                            key="agi"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            transition={{ duration: 0.2, ease: "easeOut" }}
                            className="absolute inset-0 flex flex-col"
                        >
                            <div className="flex-none p-3 border-b border-white/5 flex items-center bg-white/5 backdrop-blur-md">
                                <div className="flex items-center gap-2">
                                    <Bot className="size-4 text-cyan-300" />
                                    <span className="text-xs font-bold text-text-main uppercase tracking-widest">AGI 摘要</span>
                                </div>
                            </div>
                            <div className="flex-1 overflow-auto p-4 space-y-4">
                                <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                                    <div className="text-[10px] uppercase tracking-[0.24em] text-text-dim">Identity</div>
                                    <div className="mt-2 text-sm font-semibold text-text-main">{resident?.identity?.name || 'Software Engineering AGI'}</div>
                                    <div className="mt-1 text-xs text-text-dim">{resident?.identity?.mission || '尚未设定任务宣言'}</div>
                                </div>

                                <div className="grid gap-3 sm:grid-cols-2">
                                    <AgiMetric label="Mode" value={resident?.runtime?.mode || resident?.identity?.operating_mode || 'observe'} />
                                    <AgiMetric label="Tick" value={String(resident?.runtime?.tick_count ?? 0)} />
                                    <AgiMetric label="Goals" value={String(resident?.counts?.goals ?? 0)} />
                                    <AgiMetric label="Decisions" value={String(resident?.counts?.decisions ?? 0)} />
                                </div>

                                <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                                    <div className="text-[10px] uppercase tracking-[0.24em] text-text-dim">Focus</div>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        {(resident?.agenda?.current_focus || []).length ? (
                                            resident?.agenda?.current_focus?.map((item) => (
                                                <span key={item} className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-100">
                                                    {item}
                                                </span>
                                            ))
                                        ) : (
                                            <span className="text-xs text-text-dim">暂无焦点</span>
                                        )}
                                    </div>
                                </div>

                                <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                                    <div className="text-[10px] uppercase tracking-[0.24em] text-text-dim">Risk Register</div>
                                    <div className="mt-2 space-y-2">
                                        {(resident?.agenda?.risk_register || []).length ? (
                                            resident?.agenda?.risk_register?.map((item) => (
                                                <div key={item} className="rounded-xl border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
                                                    {item}
                                                </div>
                                            ))
                                        ) : (
                                            <span className="text-xs text-text-dim">暂无风险</span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

function AgiMetric({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-2xl border border-white/10 bg-black/20 p-3">
            <div className="text-[10px] uppercase tracking-[0.24em] text-text-dim">{label}</div>
            <div className="mt-2 text-sm font-semibold text-text-main">{value}</div>
        </div>
    );
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
    return (
        <button
            onClick={onClick}
            className={`group relative flex flex-col items-center justify-center p-3 rounded-2xl transition-all duration-500 ${active ? 'bg-white/10 text-accent shadow-[0_0_20px_rgba(139,92,246,0.3)] border border-white/10' : 'text-text-muted hover:text-white hover:bg-white/5'}`}
            title={label}
        >
            {active && (
                <motion.div
                    layoutId="activeTabIndicator"
                    className="absolute -left-1 w-1 h-8 bg-accent rounded-r shadow-glow"
                />
            )}
            <div className={`transition-all duration-500 ${active ? 'scale-110' : 'group-hover:scale-110 group-hover:drop-shadow-[0_0_8px_rgba(255,255,255,0.3)]'}`}>
                {icon}
            </div>
        </button>
    );
}

