/**
 * ProgressBar 组件测试
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProgressBar } from '../ProgressBar';

describe('ProgressBar', () => {
    it('应该正确显示进度百分比', () => {
        render(
            <ProgressBar
                progress={0.75}
                progressHint="已完成 3/4"
                progressMode="done"
                totalTasks={4}
                completedCount={3}
            />
        );

        expect(screen.getByText('75%')).toBeInTheDocument();
        expect(screen.getByText('已完成 3/4')).toBeInTheDocument();
    });

    it('应该显示正确的进度模式', () => {
        const { rerender } = render(
            <ProgressBar
                progress={0.5}
                progressHint="当前任务 2/4"
                progressMode="position"
                totalTasks={4}
                completedCount={2}
            />
        );

        expect(screen.getByText('进行中')).toBeInTheDocument();

        rerender(
            <ProgressBar
                progress={1}
                progressHint="已完成 4/4"
                progressMode="done"
                totalTasks={4}
                completedCount={4}
            />
        );

        expect(screen.getByText('已完成')).toBeInTheDocument();
    });

    it('应该显示任务统计信息', () => {
        render(
            <ProgressBar
                progress={0.5}
                progressHint="已完成 2/4"
                progressMode="done"
                totalTasks={4}
                completedCount={2}
            />
        );

        expect(screen.getByText(/总任务: 4/)).toBeInTheDocument();
        expect(screen.getByText(/已完成: 2/)).toBeInTheDocument();
    });

    it('应该显示成功率（当提供时）', () => {
        render(
            <ProgressBar
                progress={0.8}
                progressHint="已完成 4/5"
                progressMode="done"
                totalTasks={5}
                completedCount={4}
                successRate={0.85}
            />
        );

        expect(screen.getByText(/Director QA Pass Rate: 85%/)).toBeInTheDocument();
    });

    it('任务数为 0 时应该显示占位符', () => {
        render(
            <ProgressBar
                progress={0}
                progressHint="等待 PM 输出任务"
                progressMode="idle"
                totalTasks={0}
                completedCount={0}
            />
        );

        expect(screen.getByText(/总任务: -/)).toBeInTheDocument();
        expect(screen.getByText(/已完成: -/)).toBeInTheDocument();
    });
});
