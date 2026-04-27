import { AlertTriangle, CheckCircle, Clock, XCircle, ExternalLink, ShieldAlert, FileText, Settings } from 'lucide-react';
import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { Badge } from '@/app/components/ui/badge';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/app/components/ui/tabs';

export interface Intervention {
  id: string;
  type: 'agent_confirmation' | 'plan_change' | 'dependency_missing' | 'policy_block' | 'manual_approval';
  status: 'pending' | 'approved' | 'rejected' | 'ignored';
  title: string;
  description: string;
  created_at: string;
  updated_at?: string;
  context?: Record<string, unknown>;
  actions?: Array<{
    label: string;
    value: string;
    style?: 'primary' | 'secondary' | 'danger' | 'ghost';
  }>;
}

interface InterventionCenterProps {
  isOpen: boolean;
  onClose: () => void;
}

export function InterventionCenter({ isOpen, onClose }: InterventionCenterProps) {
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('pending');
  const [selectedIntervention, setSelectedIntervention] = useState<Intervention | null>(null);

  const fetchInterventions = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/interventions/list');
      if (res.ok) {
        const data = await res.json();
        setInterventions(data.interventions || []);
      }
    } catch (error) {
      devLogger.error('Failed to fetch interventions:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchInterventions();
    }
  }, [isOpen]);

  const handleAction = async (interventionId: string, actionValue: string) => {
    try {
      const res = await apiFetch('/interventions/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: interventionId, action: actionValue }),
      });
      if (res.ok) {
        fetchInterventions();
        setSelectedIntervention(null);
      }
    } catch (error) {
      devLogger.error('Failed to perform action:', error);
    }
  };

  const getIcon = (type: string) => {
    switch (type) {
      case 'agent_confirmation':
        return <UserCog className="h-4 w-4 text-blue-400" />;
      case 'plan_change':
        return <FileText className="h-4 w-4 text-amber-400" />;
      case 'dependency_missing':
        return <AlertTriangle className="h-4 w-4 text-red-400" />;
      case 'policy_block':
        return <ShieldAlert className="h-4 w-4 text-purple-400" />;
      case 'manual_approval':
        return <CheckCircle className="h-4 w-4 text-emerald-400" />;
      default:
        return <Settings className="h-4 w-4 text-gray-400" />;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'pending':
        return <Badge variant="outline" className="bg-yellow-500/10 text-yellow-500 border-yellow-500/20">待裁</Badge>;
      case 'approved':
        return <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20">已准</Badge>;
      case 'rejected':
        return <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20">驳回</Badge>;
      case 'ignored':
        return <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">略过</Badge>;
      default:
        return null;
    }
  };

  const getStatusText = (status: Intervention['status']) => {
    switch (status) {
      case 'pending':
        return '待裁';
      case 'approved':
        return '已准';
      case 'rejected':
        return '驳回';
      case 'ignored':
        return '略过';
      default:
        return status;
    }
  };

  const getTypeLabel = (type: Intervention['type']) => {
    switch (type) {
      case 'agent_confirmation':
        return '角色请裁';
      case 'plan_change':
        return '方案变更';
      case 'dependency_missing':
        return '依赖缺失';
      case 'policy_block':
        return '门禁阻断';
      case 'manual_approval':
        return '人工核准';
      default:
        return type;
    }
  };

  const filteredInterventions = interventions.filter(i => {
    if (activeTab === 'pending') return i.status === 'pending';
    if (activeTab === 'history') return i.status !== 'pending';
    return true;
  });

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-4xl h-[80vh] flex flex-col bg-[var(--ink-indigo)] border-gray-800 text-gray-200 p-0 gap-0">
        <DialogHeader className="p-6 border-b border-gray-800">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-xl font-semibold flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-emerald-500" />
              Intervention Center
            </DialogTitle>
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-[200px]">
              <TabsList className="grid w-full grid-cols-2 bg-gray-800">
                <TabsTrigger value="pending">待裁 ({interventions.filter(i => i.status === 'pending').length})</TabsTrigger>
                <TabsTrigger value="history">已决</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <DialogDescription className="text-gray-400 mt-2">
            集中处理人工核准、门禁阻断与关键告警。
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 flex overflow-hidden">
          {/* List */}
          <div className="w-1/3 border-r border-gray-800 flex flex-col">
            <ScrollArea className="flex-1">
              <div className="p-4 space-y-2">
                {loading ? (
                  <div className="text-center text-gray-500 py-8">加载中...</div>
                ) : filteredInterventions.length === 0 ? (
                  <div className="text-center text-gray-500 py-8">暂无介入事项。</div>
                ) : (
                  filteredInterventions.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => setSelectedIntervention(item)}
                      className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                        selectedIntervention?.id === item.id
                          ? 'bg-emerald-500/10 border-emerald-500/30'
                          : 'bg-gray-800/30 border-gray-800 hover:bg-gray-800/50'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          {getIcon(item.type)}
                          <span className="text-xs font-medium text-gray-300 truncate max-w-[120px]">{getTypeLabel(item.type)}</span>
                        </div>
                        <span className="text-[10px] text-gray-500">{new Date(item.created_at).toLocaleTimeString()}</span>
                      </div>
                      <div className="font-medium text-sm text-gray-200 mb-1">{item.title}</div>
                      <div className="flex items-center justify-between">
                        {getStatusBadge(item.status)}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </ScrollArea>
          </div>

          {/* Detail */}
          <div className="flex-1 flex flex-col bg-[#141414]">
            {selectedIntervention ? (
              <>
                <ScrollArea className="flex-1 p-6">
                  <div className="space-y-6">
                    <div>
                      <h2 className="text-lg font-semibold text-white mb-2">{selectedIntervention.title}</h2>
                      <div className="flex items-center gap-3 text-sm text-gray-400">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {new Date(selectedIntervention.created_at).toLocaleString()}
                        </span>
                        <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-xs font-mono">
                          ID: {selectedIntervention.id.slice(0, 8)}
                        </span>
                      </div>
                    </div>

                    <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                      <h3 className="text-sm font-medium text-gray-300 mb-2">缘由</h3>
                      <p className="text-sm text-gray-400 whitespace-pre-wrap">{selectedIntervention.description}</p>
                    </div>

                    {selectedIntervention.context && (
                      <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
                        <h3 className="text-sm font-medium text-gray-300 mb-2">上下文凭据</h3>
                        <pre className="text-xs text-gray-400 font-mono overflow-x-auto">
                          {JSON.stringify(selectedIntervention.context, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </ScrollArea>

                <div className="p-6 border-t border-gray-800 bg-[var(--ink-indigo)]">
                  <div className="flex items-center justify-end gap-3">
                    {selectedIntervention.status === 'pending' ? (
                      selectedIntervention.actions?.map((action) => (
                        <Button
                          key={action.value}
                          onClick={() => handleAction(selectedIntervention.id, action.value)}
                          variant={action.style === 'danger' ? 'destructive' : action.style === 'secondary' ? 'secondary' : action.style === 'ghost' ? 'ghost' : 'default'}
                          className="min-w-[100px]"
                        >
                          {action.label}
                        </Button>
                      )) || (
                        <>
                          <Button variant="ghost" onClick={() => handleAction(selectedIntervention.id, 'ignore')}>略过</Button>
                          <Button variant="default" onClick={() => handleAction(selectedIntervention.id, 'approve')}>准行</Button>
                        </>
                      )
                    ) : (
                      <div className="text-sm text-gray-500 italic">
                        此事项已处理：{getStatusText(selectedIntervention.status)}。
                      </div>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-gray-500 flex-col gap-2">
                <ShieldAlert className="h-12 w-12 opacity-20" />
                <p>请选择左侧事项查看详情</p>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// Helper icon component since UserCog is not exported from lucide-react in all versions
function UserCog({ className }: { className?: string }) {
  return (
    <svg 
      xmlns="http://www.w3.org/2000/svg" 
      width="24" 
      height="24" 
      viewBox="0 0 24 24" 
      fill="none" 
      stroke="currentColor" 
      strokeWidth="2" 
      strokeLinecap="round" 
      strokeLinejoin="round" 
      className={className}
    >
      <circle cx="18" cy="15" r="3" />
      <circle cx="9" cy="7" r="4" />
      <path d="M10 15H6a4 4 0 0 0-4 4v2" />
      <path d="m21.7 16.4.9-.9" />
      <path d="m15.3 10 .9.9" />
      <path d="m21.7 13.6.9.9" />
      <path d="m15.3 12.8.9-.9" />
      <path d="m17.2 18.2.9-.9" />
      <path d="m17.2 11.8.9-.9" />
    </svg>
  );
}
