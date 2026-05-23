import { ReactFlowProvider } from '@xyflow/react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { VisualModelNode } from './VisualModelNode';

describe('VisualModelNode', () => {
  it('renders assigned Director and Chief Engineer role chips distinctly', () => {
    render(
      <ReactFlowProvider>
        <VisualModelNode
          {...({
            id: 'model:qwen3-max',
            type: 'model',
            selected: false,
            isConnectable: true,
            data: {
              kind: 'model',
              providerId: 'openai_compat',
              model: 'qwen3-max',
              label: 'qwen3-max',
              assignedRoles: ['director', 'chief_engineer'],
            },
          } as never)}
        />
      </ReactFlowProvider>
    );

    expect(screen.getByText('Director')).toBeInTheDocument();
    expect(screen.getByText('Chief Engineer')).toBeInTheDocument();
  });
});
