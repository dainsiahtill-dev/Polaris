import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectProgressPanel } from '../../ProjectProgressPanel';

describe('ProjectProgressPanel', () => {
    it('renders with structured runtime focus and notes payloads', () => {
        render(
            <ProjectProgressPanel
                tasks={[]}
                focus={{ stage: 'pm', detail: 'structured focus' }}
                notes={['structured note']}
            />,
        );

        expect(screen.getByTestId('project-progress-panel')).toBeInTheDocument();
        expect(screen.getByText('PM 政务进度')).toBeInTheDocument();
    });
});
