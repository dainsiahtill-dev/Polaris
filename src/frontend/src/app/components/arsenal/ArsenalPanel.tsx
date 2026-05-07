import React, { useCallback, useEffect, useState } from 'react';
import { CodeMap3D } from './CodeMap3D';
import { Card } from '@/app/components/ui/card';
import { Button } from '@/app/components/ui/button';
import { Loader2, RefreshCcw, Box } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/app/components/ui/alert';
import { apiFetch } from '@/api';

interface MapData {
  points: { path: string; x: number; y: number; z: number; cluster: number }[];
  mode: string;
  engine_active: boolean;
}

export function ArsenalPanel() {
  const [data, setData] = useState<MapData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/arsenal/code_map', { signal });
      if (!res.ok) throw new Error('拉取军械库图谱失败');
      const json = await res.json();
      if (signal?.aborted) return;
      setData(json);
    } catch (err: unknown) {
      if (signal?.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
        return;
      }
      const message = err instanceof Error ? err.message : '拉取军械库图谱失败';
      setError(message);
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetchData(controller.signal);
    return () => controller.abort();
  }, [fetchData]);

  return (
    <div className="space-y-4 text-text-main h-full">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Box className="w-5 h-5 text-cyan-400" />
            Polaris 军械库
          </h2>
          <p className="text-sm text-text-dim">重型可视化与算力模块</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void fetchData()}
          disabled={loading}
          className="border-white/10 hover:bg-white/5"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <RefreshCcw className="w-4 h-4 mr-2" />}
          重新分析
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>异常</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card className="p-4 bg-black/20 border-white/5">
        <div className="mb-4 flex items-center justify-between text-xs text-text-dim">
          <span>图谱视图：三维代码舆图</span>
          <span>运行模式：{data?.mode?.toUpperCase() || '未判'}</span>
        </div>

        {loading && !data ? (
          <div className="h-[500px] flex items-center justify-center border border-white/5 rounded bg-black/40">
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="w-8 h-8 animate-spin text-cyan-400" />
              <span className="text-sm text-cyan-400/80">正在分析代码结构...</span>
            </div>
          </div>
        ) : (
          data?.points && <CodeMap3D points={data.points} />
        )}

        <div className="mt-4 text-xs text-text-muted">
          已索引 {data?.points?.length || 0} 个文件，当前引擎：{data?.engine_active ? '扩展引擎' : '标准引擎'}。
        </div>
      </Card>
    </div>
  );
}
