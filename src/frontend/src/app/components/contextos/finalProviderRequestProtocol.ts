export interface FinalProviderRequestPayload {
  schema_version?: string;
  context_hash?: string;
  trace_id?: string | null;
  call_id?: string | null;
  stored_at?: string | null;
  message_count?: number;
  provider_request?: Record<string, unknown>;
  provider_request_schema_version?: string;
  role?: string;
  provider_id?: string;
  provider_type?: string;
  model?: string;
  tools?: Array<Record<string, unknown>>;
  tool_choice?: unknown;
  response_format?: unknown;
  final_request_context_audit?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function normalizeFinalProviderRequestPayload(raw: unknown): FinalProviderRequestPayload | null {
  if (!isRecord(raw)) return null;
  const providerRequest = isRecord(raw.provider_request) ? raw.provider_request : {};
  const finalAudit = isRecord(raw.final_request_context_audit) ? raw.final_request_context_audit : {};
  const tools = Array.isArray(raw.tools)
    ? raw.tools.filter((item): item is Record<string, unknown> => isRecord(item))
    : [];
  return {
    schema_version: typeof raw.schema_version === 'string' ? raw.schema_version : undefined,
    context_hash: typeof raw.context_hash === 'string' ? raw.context_hash : undefined,
    trace_id: typeof raw.trace_id === 'string' ? raw.trace_id : null,
    call_id: typeof raw.call_id === 'string' ? raw.call_id : null,
    stored_at: typeof raw.stored_at === 'string' ? raw.stored_at : null,
    message_count: typeof raw.message_count === 'number' ? raw.message_count : undefined,
    provider_request: providerRequest,
    provider_request_schema_version:
      typeof raw.provider_request_schema_version === 'string' ? raw.provider_request_schema_version : undefined,
    role: typeof raw.role === 'string' ? raw.role : undefined,
    provider_id: typeof raw.provider_id === 'string' ? raw.provider_id : undefined,
    provider_type: typeof raw.provider_type === 'string' ? raw.provider_type : undefined,
    model: typeof raw.model === 'string' ? raw.model : undefined,
    tools,
    tool_choice: raw.tool_choice,
    response_format: raw.response_format,
    final_request_context_audit: finalAudit,
  };
}
