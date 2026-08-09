function isRecord(value) {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}
export function normalizeFinalProviderRequestPayload(raw) {
    if (!isRecord(raw))
        return null;
    const providerRequest = isRecord(raw.provider_request) ? raw.provider_request : {};
    const finalAudit = isRecord(raw.final_request_context_audit) ? raw.final_request_context_audit : {};
    const tools = Array.isArray(raw.tools)
        ? raw.tools.filter((item) => isRecord(item))
        : [];
    return {
        schema_version: typeof raw.schema_version === 'string' ? raw.schema_version : undefined,
        context_hash: typeof raw.context_hash === 'string' ? raw.context_hash : undefined,
        trace_id: typeof raw.trace_id === 'string' ? raw.trace_id : null,
        call_id: typeof raw.call_id === 'string' ? raw.call_id : null,
        stored_at: typeof raw.stored_at === 'string' ? raw.stored_at : null,
        message_count: typeof raw.message_count === 'number' ? raw.message_count : undefined,
        provider_request: providerRequest,
        provider_request_schema_version: typeof raw.provider_request_schema_version === 'string' ? raw.provider_request_schema_version : undefined,
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
