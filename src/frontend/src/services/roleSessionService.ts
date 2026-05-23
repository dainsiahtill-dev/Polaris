import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';

export type RoleSessionSnapshotExportFormat = 'json' | 'markdown';

export interface RoleSessionListItem {
  id: string;
  title?: string;
  role?: string;
  host_kind?: string;
  state?: string;
  attachment_mode?: string;
  message_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface RoleSessionDetailItem extends RoleSessionListItem {
  session_type?: string;
  workspace?: string;
  attached_run_id?: string | null;
  attached_task_id?: string | null;
  context_config?: Record<string, unknown>;
  capability_profile?: Record<string, unknown> | null;
}

export interface RoleSessionArtifactItem {
  id: string;
  type?: string;
  content?: unknown;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface RoleSessionAuditEventItem {
  id?: string;
  event_type?: string;
  type?: string;
  timestamp?: string;
  created_at?: string;
  actor?: string;
  payload?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface RoleSessionMemoryItem {
  id?: string;
  kind?: string;
  entity?: string;
  path?: string;
  text?: string;
  content?: string;
  score?: number;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface RoleSessionMemoryDetailItem {
  id: string;
  kind: string;
  payload: unknown;
}

export interface RoleCapabilitiesResponse {
  ok?: boolean;
  role?: string;
  capabilities?: Record<string, string[]> | string[];
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionResponse {
  ok?: boolean;
  session?: RoleSessionDetailItem | null;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionListResponse {
  ok?: boolean;
  sessions?: RoleSessionListItem[];
  total?: number;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionMessageItem {
  id?: string;
  role?: string;
  content?: string;
  thinking?: string | null;
  created_at?: string;
  [key: string]: unknown;
}

export interface RoleSessionMessagesResponse {
  ok?: boolean;
  messages?: RoleSessionMessageItem[];
  session?: RoleSessionDetailItem | null;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionArtifactsResponse {
  ok?: boolean;
  artifacts?: RoleSessionArtifactItem[];
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionAuditResponse {
  ok?: boolean;
  audit_events?: RoleSessionAuditEventItem[];
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionMemorySearchResponse {
  ok?: boolean;
  session_id?: string;
  query?: string;
  total?: number;
  items?: RoleSessionMemoryItem[];
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionArtifactDetailResponse {
  ok?: boolean;
  artifact?: unknown;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionEpisodeDetailResponse {
  ok?: boolean;
  episode?: unknown;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionMemoryStateResponse {
  ok?: boolean;
  value?: unknown;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionSnapshotExportResponse {
  ok?: boolean;
  export?: unknown;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionWorkflowExportResponse {
  ok?: boolean;
  exported_to?: string;
  run_id?: string;
  session_id?: string;
  artifact_count?: number;
  error?: string;
  detail?: string;
  message?: string;
}

export interface RoleSessionAttachmentResponse {
  ok?: boolean;
  attachment?: Record<string, unknown> | null;
  session?: RoleSessionDetailItem | null;
  error?: string;
  detail?: string;
  message?: string;
}

export interface CreateRoleSessionPayload {
  role: string;
  host_kind: string;
  workspace?: string;
  attachment_mode: string;
  context_config?: Record<string, unknown>;
  capability_profile?: Record<string, unknown>;
}

export interface AttachRoleSessionPayload {
  run_id: string | null;
  task_id: string | null;
  mode: string;
  note?: string;
}

export interface ExportRoleSessionSnapshotPayload {
  include_messages: boolean;
  format: RoleSessionSnapshotExportFormat;
}

export interface ExportRoleSessionToWorkflowPayload {
  target: 'pm' | 'director' | 'factory';
  export_kind: 'session_bundle' | 'artifacts_only' | 'messages_only';
  include_audit_log: boolean;
}

export interface ListRoleSessionsParams {
  role: string;
  hostKind: string;
  workspace?: string;
  limit?: number;
  offset?: number;
}

function responseError(payload: { error?: string; detail?: string; message?: string } | undefined, fallback: string): string {
  return String(payload?.error || payload?.detail || payload?.message || fallback);
}

function fail<T>(error: string): ApiResult<T> {
  return { ok: false, error };
}

function isRoleSessionListItem(value: unknown): value is RoleSessionListItem {
  return Boolean(value && typeof value === 'object' && String((value as { id?: unknown }).id || '').trim());
}

function normalizeRoleSessionList(items: unknown): RoleSessionListItem[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter(isRoleSessionListItem)
    .map((item) => item as RoleSessionListItem);
}

function normalizeArtifacts(items: unknown): RoleSessionArtifactItem[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map((item) => item && typeof item === 'object' ? item as RoleSessionArtifactItem : null)
    .filter((artifact): artifact is RoleSessionArtifactItem => Boolean(artifact?.id));
}

function normalizeAuditEvents(items: unknown): RoleSessionAuditEventItem[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map((item) => item && typeof item === 'object' ? item as RoleSessionAuditEventItem : null)
    .filter((event): event is RoleSessionAuditEventItem => Boolean(event));
}

function normalizeMemoryItems(items: unknown): RoleSessionMemoryItem[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map((item) => item && typeof item === 'object' ? item as RoleSessionMemoryItem : null)
    .filter((item): item is RoleSessionMemoryItem => Boolean(item));
}

export function resolveRoleCapabilities(
  payload: RoleCapabilitiesResponse | null | undefined,
  hostKind: string,
): string[] {
  const capabilities = payload?.capabilities;
  if (Array.isArray(capabilities)) {
    return capabilities.map((item) => String(item || '').trim()).filter(Boolean);
  }

  if (!capabilities || typeof capabilities !== 'object') {
    return [];
  }

  const record = capabilities as Record<string, unknown>;
  const hostCapabilities = record[hostKind] || record.default;
  return Array.isArray(hostCapabilities)
    ? hostCapabilities.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
}

export async function getRoleCapabilities(
  role: string,
  hostKind: string,
): Promise<ApiResult<RoleCapabilitiesResponse>> {
  return apiGet<RoleCapabilitiesResponse>(
    `/v2/roles/capabilities/${encodeURIComponent(role)}?host_kind=${encodeURIComponent(hostKind)}`,
    'Failed to load role capabilities',
  );
}

export async function getRoleSession(sessionId: string): Promise<ApiResult<RoleSessionDetailItem>> {
  const result = await apiGet<RoleSessionResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}`,
    'Failed to load RoleSession',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to load RoleSession');
  }
  if (result.data.ok === false || !result.data.session || typeof result.data.session !== 'object') {
    return fail(responseError(result.data, 'RoleSession response missing session'));
  }
  return { ok: true, data: result.data.session };
}

export async function createRoleSession(
  payload: CreateRoleSessionPayload,
): Promise<ApiResult<RoleSessionDetailItem>> {
  const result = await apiPost<RoleSessionResponse>(
    '/v2/roles/sessions',
    payload,
    'Failed to create RoleSession',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to create RoleSession');
  }
  if (result.data.ok === false || !result.data.session || typeof result.data.session !== 'object') {
    return fail(responseError(result.data, 'RoleSession create response missing session id'));
  }
  return { ok: true, data: result.data.session };
}

export async function attachRoleSession(
  sessionId: string,
  payload: AttachRoleSessionPayload,
): Promise<ApiResult<RoleSessionAttachmentResponse>> {
  const result = await apiPost<RoleSessionAttachmentResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/attach`,
    payload,
    'Failed to attach RoleSession',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to attach RoleSession');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to attach RoleSession'));
  }
  return { ok: true, data: result.data };
}

export async function detachRoleSession(sessionId: string): Promise<ApiResult<RoleSessionDetailItem | null>> {
  const result = await apiPost<RoleSessionResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/detach`,
    {},
    'Failed to detach RoleSession',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to detach RoleSession');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to detach RoleSession'));
  }
  return { ok: true, data: result.data.session ?? null };
}

export async function listRoleSessions(
  params: ListRoleSessionsParams,
): Promise<ApiResult<RoleSessionListItem[]>> {
  const query = new URLSearchParams({
    role: params.role,
    host_kind: params.hostKind,
    limit: String(params.limit ?? 20),
  });
  if (typeof params.offset === 'number') {
    query.set('offset', String(params.offset));
  }
  if (params.workspace) {
    query.set('workspace', params.workspace);
  }

  const result = await apiGet<RoleSessionListResponse>(
    `/v2/roles/sessions?${query.toString()}`,
    'Failed to list RoleSessions',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to list RoleSessions');
  }
  if (result.data.ok === false || !Array.isArray(result.data.sessions)) {
    return fail(responseError(result.data, 'RoleSession list response missing sessions'));
  }
  return { ok: true, data: normalizeRoleSessionList(result.data.sessions) };
}

export async function listRoleSessionMessages(
  sessionId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<ApiResult<RoleSessionMessageItem[]>> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 100),
    offset: String(params.offset ?? 0),
  });
  const result = await apiGet<RoleSessionMessagesResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/messages?${query.toString()}`,
    'Failed to list RoleSession messages',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to list RoleSession messages');
  }
  if (result.data.ok === false || !Array.isArray(result.data.messages)) {
    return fail(responseError(result.data, 'RoleSession messages response missing messages'));
  }
  return { ok: true, data: result.data.messages };
}

export async function listRoleSessionArtifacts(
  sessionId: string,
  artifactType?: string,
): Promise<ApiResult<RoleSessionArtifactItem[]>> {
  const query = artifactType ? `?artifact_type=${encodeURIComponent(artifactType)}` : '';
  const result = await apiGet<RoleSessionArtifactsResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/artifacts${query}`,
    'Failed to list RoleSession artifacts',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to list RoleSession artifacts');
  }
  if (result.data.ok === false || !Array.isArray(result.data.artifacts)) {
    return fail(responseError(result.data, 'RoleSession artifacts response missing artifacts'));
  }
  return { ok: true, data: normalizeArtifacts(result.data.artifacts) };
}

export async function listRoleSessionAuditEvents(
  sessionId: string,
  params: { eventType?: string; limit?: number; offset?: number } = {},
): Promise<ApiResult<RoleSessionAuditEventItem[]>> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 20),
    offset: String(params.offset ?? 0),
  });
  if (params.eventType) {
    query.set('event_type', params.eventType);
  }
  const result = await apiGet<RoleSessionAuditResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/audit?${query.toString()}`,
    'Failed to list RoleSession audit events',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to list RoleSession audit events');
  }
  if (result.data.ok === false || !Array.isArray(result.data.audit_events)) {
    return fail(responseError(result.data, 'RoleSession audit response missing events'));
  }
  return { ok: true, data: normalizeAuditEvents(result.data.audit_events) };
}

export async function searchRoleSessionMemory(
  sessionId: string,
  queryText: string,
  params: { limit?: number; kind?: string; entity?: string } = {},
): Promise<ApiResult<RoleSessionMemoryItem[]>> {
  const query = new URLSearchParams({
    q: queryText,
    limit: String(params.limit ?? 8),
  });
  if (params.kind) {
    query.set('kind', params.kind);
  }
  if (params.entity) {
    query.set('entity', params.entity);
  }
  const result = await apiGet<RoleSessionMemorySearchResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/search?${query.toString()}`,
    'Failed to search RoleSession memory',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to search RoleSession memory');
  }
  if (result.data.ok === false || !Array.isArray(result.data.items)) {
    return fail(responseError(result.data, 'RoleSession memory search response missing items'));
  }
  return { ok: true, data: normalizeMemoryItems(result.data.items) };
}

export async function readRoleSessionMemoryArtifact(
  sessionId: string,
  artifactId: string,
): Promise<ApiResult<unknown>> {
  const result = await apiGet<RoleSessionArtifactDetailResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/artifacts/${encodeURIComponent(artifactId)}`,
    'Failed to read RoleSession memory artifact',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to read RoleSession memory artifact');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to read RoleSession memory artifact'));
  }
  return { ok: true, data: result.data.artifact ?? result.data };
}

export async function readRoleSessionMemoryEpisode(
  sessionId: string,
  episodeId: string,
): Promise<ApiResult<unknown>> {
  const result = await apiGet<RoleSessionEpisodeDetailResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/episodes/${encodeURIComponent(episodeId)}`,
    'Failed to read RoleSession memory episode',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to read RoleSession memory episode');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to read RoleSession memory episode'));
  }
  return { ok: true, data: result.data.episode ?? result.data };
}

export async function readRoleSessionMemoryState(
  sessionId: string,
  statePath: string,
): Promise<ApiResult<unknown>> {
  const result = await apiGet<RoleSessionMemoryStateResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/state?path=${encodeURIComponent(statePath)}`,
    'Failed to read RoleSession memory state',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to read RoleSession memory state');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to read RoleSession memory state'));
  }
  return { ok: true, data: result.data.value ?? result.data };
}

export async function exportRoleSessionSnapshot(
  sessionId: string,
  payload: ExportRoleSessionSnapshotPayload,
): Promise<ApiResult<unknown>> {
  const result = await apiPost<RoleSessionSnapshotExportResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/export`,
    payload,
    'Failed to export RoleSession snapshot',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to export RoleSession snapshot');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to export RoleSession snapshot'));
  }
  return { ok: true, data: result.data.export ?? null };
}

export async function exportRoleSessionToWorkflow(
  sessionId: string,
  payload: ExportRoleSessionToWorkflowPayload,
): Promise<ApiResult<RoleSessionWorkflowExportResponse>> {
  const result = await apiPost<RoleSessionWorkflowExportResponse>(
    `/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/export-to-workflow`,
    payload,
    'Failed to export RoleSession to workflow',
  );
  if (!result.ok || !result.data) {
    return fail(result.error || 'Failed to export RoleSession to workflow');
  }
  if (result.data.ok === false) {
    return fail(responseError(result.data, 'Failed to export RoleSession to workflow'));
  }
  return { ok: true, data: result.data };
}
