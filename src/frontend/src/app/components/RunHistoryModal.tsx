import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { History, CheckCircle, XCircle, Clock, FileText, ArrowRight } from 'lucide-react';
import { LogsModal } from '@/app/components/LogsModal';
import { UI_TERMS } from '@/app/constants/uiTerminology';

interface RunItem {
  id: string;
  status?: string;
  task_id?: string;
  timestamp?: string;
  duration?: number;
  risk_score?: number;
  error_code?: string;
  tool_rounds?: number;
  total_lines_read?: number;
}

interface RunHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function RunHistoryModal({ isOpen, onClose }: RunHistoryModalProps) {
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRun, setSelectedRun] = useState<RunItem | null>(null);
  const [showLogs, setShowLogs] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      apiFetch('/history/runs')
        .then((res) => res.json())
        .then((data) => {
          setRuns(data.runs || []);
          if (data.runs && data.runs.length > 0 && !selectedRun) {
            setSelectedRun(data.runs[0]);
          }
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [isOpen]);

  const handleRunClick = (run: RunItem) => {
    setSelectedRun(run);
  };

  return (
    <>
      <Dialog open={isOpen} onOpenChange={onClose}>
        <DialogContent className="max-w-5xl h-[80vh] bg-[var(--ink-indigo)] border-gray-800 text-gray-200 flex flex-col p-0 gap-0">
          <DialogHeader className="p-6 border-b border-gray-800">
            <DialogTitle className="text-xl font-semibold flex items-center gap-2">
              <History className="h-5 w-5 text-blue-400" />
              {UI_TERMS.nouns.history}
            </DialogTitle>
          </DialogHeader>
          
          <div className="flex-1 flex overflow-hidden">
            {/* Left List */}
            <div className="w-1/3 border-r border-gray-800 flex flex-col">
              <ScrollArea className="flex-1">
                <div className="p-2 space-y-1">
                  {loading ? (
                    <div className="text-center text-gray-500 py-8">Loading history...</div>
                  ) : runs.length === 0 ? (
                    <div className="text-center text-gray-500 py-8">No history yet.</div>
                  ) : (
                    runs.map((run) => (
                      <div
                        key={run.id}
                        onClick={() => handleRunClick(run)}
                        className={`p-3 rounded-lg cursor-pointer transition-colors border ${
                          selectedRun?.id === run.id
                            ? 'bg-blue-500/10 border-blue-500/30'
                            : 'bg-transparent border-transparent hover:bg-gray-800/50'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className={`font-mono text-xs ${selectedRun?.id === run.id ? 'text-blue-300' : 'text-gray-400'}`}>
                            {run.id}
                          </span>
                          {run.status === 'success' ? (
                            <CheckCircle className="h-3 w-3 text-emerald-400" />
                          ) : run.status === 'fail' ? (
                            <XCircle className="h-3 w-3 text-red-400" />
                          ) : (
                            <span className="text-[10px] text-gray-500">{run.status || '?'}</span>
                          )}
                        </div>
                        <div className="text-xs text-gray-300 truncate mb-1">
                          {run.task_id || '（No task ID）'}
                        </div>
                        <div className="text-[10px] text-gray-500 flex justify-between">
                          <span>{run.timestamp?.split(' ')[1] || '-'}</span>
                          {run.duration && <span>{run.duration.toFixed(1)}s</span>}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>

            {/* Right Detail */}
            <div className="flex-1 bg-[#141414] flex flex-col">
              {selectedRun ? (
                <div className="flex-1 flex flex-col">
                  <ScrollArea className="flex-1 p-6">
                    <div className="space-y-6">
                      {/* Header */}
                      <div>
                        <div className="flex items-center gap-3 mb-2">
                          <h2 className="text-2xl font-mono font-semibold text-white">{selectedRun.id}</h2>
                          <div className={`px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider border ${
                            selectedRun.status === 'success' 
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              : selectedRun.status === 'fail'
                                ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                : 'bg-gray-800 text-gray-400 border-gray-700'
                          }`}>
                            {selectedRun.status === 'success' ? UI_TERMS.states.success : selectedRun.status === 'fail' ? UI_TERMS.states.failed : UI_TERMS.states.unknown}
                          </div>
                        </div>
                        <div className="flex items-center gap-4 text-sm text-gray-400">
                          <span className="flex items-center gap-1">
                            <Clock className="h-4 w-4" />
                            {selectedRun.timestamp}
                          </span>
                          {selectedRun.duration && (
                            <span>耗时: <span className="text-gray-200">{selectedRun.duration.toFixed(1)}s</span></span>
                          )}
                        </div>
                      </div>

                      {/* Task Info */}
                      <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                        <h3 className="text-sm font-medium text-gray-300 mb-2">Task</h3>
                        <div className="text-sm text-gray-200 font-mono bg-black/20 p-2 rounded">
                          {selectedRun.task_id || 'Not recorded'}
                        </div>
                        {selectedRun.error_code && (
                          <div className="mt-3">
                            <div className="text-xs text-red-400 font-semibold mb-1">Failure Reason</div>
                            <div className="text-sm text-red-300">{selectedRun.error_code}</div>
                          </div>
                        )}
                      </div>

                      {/* Metrics */}
                      <div className="grid grid-cols-2 gap-4">
                        <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                          <div className="text-xs text-gray-400 mb-1">风险分</div>
                          <div className="text-xl font-semibold text-gray-200">{selectedRun.risk_score ?? '-'}</div>
                        </div>
                        <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                          <div className="text-xs text-gray-400 mb-1">工具轮次</div>
                          <div className="text-xl font-semibold text-gray-200">{selectedRun.tool_rounds ?? '-'}</div>
                        </div>
                        <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                          <div className="text-xs text-gray-400 mb-1">读取行数</div>
                          <div className="text-xl font-semibold text-gray-200">{selectedRun.total_lines_read ?? '-'}</div>
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="pt-4">
                        <Button 
                          onClick={() => setShowLogs(true)}
                          className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500"
                        >
                          <FileText className="h-4 w-4" />
                          View Logs & Artifacts
                        </Button>
                      </div>
                    </div>
                  </ScrollArea>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-gray-500 flex-col gap-2">
                  <ArrowRight className="h-8 w-8 opacity-20" />
                  <p>Select a history item to view details</p>
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <LogsModal
        isOpen={showLogs}
        onClose={() => setShowLogs(false)}
        runId={selectedRun?.id}
        banner={`正在调阅历史案卷日志：${selectedRun?.id}`}
      />
    </>
  );
}
