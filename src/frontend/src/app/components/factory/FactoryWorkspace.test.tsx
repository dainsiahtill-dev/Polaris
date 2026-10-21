import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { makeEngineeringBinding } from '@/test/engineeringRunFixtures';
import { FactoryWorkspace } from './FactoryWorkspace';
describe('FactoryWorkspace engineering retirement', () => {
  it('fails visibly without a binding and exposes no retired write controls', () => {
    const oldWrite = vi.fn();
    render(<FactoryWorkspace workspace="/work" tasks={[]} onBackToMain={() => { }} onStart={oldWrite} />);
    expect(screen.getByRole('alert')).toHaveTextContent('工程运行绑定不可用');
    expect(screen.queryByRole('button', { name: /开始执行|启动|重试|继续|取消/ })).not.toBeInTheDocument();
    expect(oldWrite).not.toHaveBeenCalled();
  });
  it.each([undefined, false, true])('uses the same engineering owner for new director work despite stale mode (%s)', (enabled) => {
    const engineering = makeEngineeringBinding();
    const oldWrite = vi.fn();
    const changeMode = vi.fn();
    render(<FactoryWorkspace workspace="/work" tasks={[]} onBackToMain={() => { }} engineering={engineering} engineeringEnabled={enabled} onEngineeringModeChange={changeMode} onStart={oldWrite} />);
    expect(screen.getByRole('heading', { name: 'Engineering v2 · Director' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: 'Requested work' } });
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    expect(engineering.start).toHaveBeenCalledWith({ instruction: 'Requested work', role: 'director', workflow: 'direct', required_checks: [] });
    expect(oldWrite).not.toHaveBeenCalled();
    expect(changeMode).not.toHaveBeenCalled();
    expect(screen.queryByText('查看历史界面')).not.toBeInTheDocument();
  });
});
