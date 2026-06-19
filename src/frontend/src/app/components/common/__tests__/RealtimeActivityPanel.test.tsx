import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RealtimeActivityPanel } from '../RealtimeActivityPanel';
import type { LogEntry } from '@/types/log';

describe('RealtimeActivityPanel', () => {
  it('preserves existing execution log source labels', () => {
    const logs: LogEntry[] = [
      {
        id: 'factory-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'thinking',
        source: 'FACTORY',
        message: 'Factory audit event',
      },
    ];

    render(<RealtimeActivityPanel executionLogs={logs} role="chief_engineer" />);

    expect(screen.getByText('FACTORY')).toBeInTheDocument();
    expect(screen.queryByText('EXEC')).not.toBeInTheDocument();
  });

  it('uses EXEC as the source label when execution logs do not provide one', () => {
    const logs: LogEntry[] = [
      {
        id: 'exec-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'thinking',
        message: 'Execution event',
      },
    ];

    render(<RealtimeActivityPanel executionLogs={logs} role="director" />);

    expect(screen.getByText('EXEC')).toBeInTheDocument();
  });

  it('auto-selects the log view when only process runtime events are available', async () => {
    const processLogs: LogEntry[] = [
      {
        id: 'process-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'info',
        source: 'Process',
        message: 'PLAYWRIGHT_NAT_JETSTREAM_MARKER visible in role workspace',
        meta: { channel: 'process', streamKind: 'execution' },
      },
    ];

    render(<RealtimeActivityPanel processStreamEvents={processLogs} role="chief_engineer" />);

    expect(await screen.findByText('PLAYWRIGHT_NAT_JETSTREAM_MARKER visible in role workspace')).toBeInTheDocument();
    expect(screen.getByText('PROC')).toBeInTheDocument();
  });

  it('shows content preview events in the default realtime stream view', () => {
    const logs: LogEntry[] = [
      {
        id: 'preview-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'info',
        source: 'Director',
        title: '输出预览',
        message: '公开模型输出片段',
        meta: { streamEvent: 'content_preview' },
      },
    ];

    render(<RealtimeActivityPanel llmStreamEvents={logs} role="director" />);

    fireEvent.click(screen.getByRole('button', { name: '查看思考记录' }));

    expect(screen.getByText('公开模型输出片段')).toBeInTheDocument();
    expect(screen.getAllByText('输出预览').length).toBeGreaterThan(0);
    expect(screen.queryByText('暂无思考记录')).not.toBeInTheDocument();
  });

  it('shows completed LLM responses in the thinking stream view', () => {
    const logs: LogEntry[] = [
      {
        id: 'completed-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'success',
        source: 'Director',
        title: 'LLM 完成',
        message: '已返回完整模型输出',
        meta: { streamEvent: 'llm_completed' },
      },
    ];

    render(<RealtimeActivityPanel llmStreamEvents={logs} role="director" />);

    fireEvent.click(screen.getByRole('button', { name: '查看思考记录' }));

    expect(screen.getByText('已返回完整模型输出')).toBeInTheDocument();
    expect(screen.queryByText('暂无思考记录')).not.toBeInTheDocument();
  });

  it('classifies broader tool stream events into the tool view', () => {
    const logs: LogEntry[] = [
      {
        id: 'tool-start-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'info',
        source: 'Director',
        message: '开始执行 write_file',
        meta: { streamEvent: 'tool_start' },
      },
    ];

    render(<RealtimeActivityPanel llmStreamEvents={logs} role="director" />);

    fireEvent.click(screen.getByRole('button', { name: '查看工具记录' }));

    expect(screen.getByText('开始执行 write_file')).toBeInTheDocument();
    expect(screen.getByText('工具事件')).toBeInTheDocument();
    expect(screen.queryByText('暂无工具记录')).not.toBeInTheDocument();
  });

  it('prioritizes real model content over lifecycle waiting events in the thinking view', () => {
    const logs: LogEntry[] = [
      {
        id: 'waiting-1',
        timestamp: '2026-05-23T00:00:02Z',
        level: 'thinking',
        source: 'Director',
        message: '正在请求 qwen3.6-27b-gpu1 响应…',
        details: 'model=qwen3.6-27b-gpu1',
        meta: { streamEvent: 'llm_waiting' },
      },
      {
        id: 'content-1',
        timestamp: '2026-05-23T00:00:01Z',
        level: 'info',
        source: 'Director',
        message: '我将先创建 index.html 并写入任务列表交互逻辑。',
        meta: { streamEvent: 'content_chunk' },
      },
    ];

    render(<RealtimeActivityPanel llmStreamEvents={logs} role="director" />);

    fireEvent.click(screen.getByRole('button', { name: '查看思考记录' }));

    const entries = screen.getAllByRole('button').map((button) => button.textContent || '');
    const contentIndex = entries.findIndex((text) => text.includes('我将先创建 index.html'));
    const waitingIndex = entries.findIndex((text) => text.includes('正在请求 qwen3.6-27b-gpu1'));

    expect(contentIndex).toBeGreaterThanOrEqual(0);
    expect(waitingIndex).toBeGreaterThanOrEqual(0);
    expect(contentIndex).toBeLessThan(waitingIndex);
  });

  it('shows file edit activity as tool evidence with the file path visible', () => {
    const logs: LogEntry[] = [
      {
        id: 'file-edit-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'tool',
        source: 'Director',
        message: '写入文件: src/main.py',
        details: 'operation=create lines=42',
        meta: { streamEvent: 'file_written', tool: 'write_file', path: 'src/main.py' },
      },
    ];

    render(<RealtimeActivityPanel executionLogs={logs} role="director" />);

    fireEvent.click(screen.getByRole('button', { name: '查看工具记录' }));

    expect(screen.getByText('写入文件: src/main.py')).toBeInTheDocument();
    expect(screen.getByText('write_file')).toBeInTheDocument();
    expect(screen.getByText('src/main.py')).toBeInTheDocument();
    expect(screen.queryByText('暂无工具记录')).not.toBeInTheDocument();
  });
});
