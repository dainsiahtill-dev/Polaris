import type { ContextFoldState, ContextNodeKind } from './contextOSNodeMapModel';

export interface ProjectionManifestNode {
  id: string;
  parent_id: string | null;
  seq: number | null;
  turn: number | null;
  kind: ContextNodeKind;
  title: string;
  summary: string;
  role: string;
  fold_state: ContextFoldState;
  fold_reason: string;
  evidence_ref: string | null;
  projection_ref: string | null;
  request_message_index: number | null;
  phase: string | null;
  ok: boolean | null;
  original_tokens: number;
  projected_tokens: number;
}

export interface ProjectionRunCard {
  id: string;
  seq: number;
  role: string;
  phase: string | null;
  verification: string;
  workspace_revision: number | null;
  child_ids: string[];
}

export interface ContextProjectionManifest {
  schema_version: 'context.projection_manifest.v1';
  context_snapshot_ref: string | null;
  workspace: string;
  run_id: string;
  call_id: string | null;
  nodes: ProjectionManifestNode[];
  run_cards: ProjectionRunCard[];
  summary: {
    original_tokens: number;
    projected_tokens: number;
    compression_ratio: number | null;
    fold_counts: Partial<Record<ContextFoldState, number>>;
    kind_counts: Partial<Record<ContextNodeKind, number>>;
    run_card_count: number;
    scope: string;
  };
}
