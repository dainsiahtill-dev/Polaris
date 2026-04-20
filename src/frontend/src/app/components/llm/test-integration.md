# Frontend Integration Test Guide

## Key Features Implemented

### 1. Conditional API Key Display ✅
- **Codex CLI**: No API key field shown
- **Ollama**: No API key field shown  
- **Gemini CLI**: Shows Google API key field
- **MiniMax**: Shows API key field
- **Gemini API**: Shows API key field
- **OpenAI Compatible**: Shows API key field
- **Anthropic Compatible**: Shows API key field

### 2. Provider-Specific Settings ✅
- **Codex CLI**: Working directory, sandbox mode, approval settings, JSON output
- **Ollama**: Server URL, GPU layers, context size, common models reference
- **Gemini CLI**: API key source, command arguments, health check args
- **MiniMax**: Model-specific settings
- **Gemini API**: Large context support, model path configuration
- **OpenAI Compatible**: Custom headers, API path configuration
- **Anthropic Compatible**: API version, custom headers

### 3. CLI Modes ✅
- **TUI mode**: Interactive guidance panels for model discovery
- **Headless mode**: Command template with `{model}` / `{prompt}` validation

### 4. Token Usage Visibility ✅
- **Test results**: Display token usage where available (input/output/total + estimated)
- **Token metrics only**

### 5. Dynamic Provider Loading ✅
- **Provider Registry**: Automatic discovery and registration
- **Component Mapping**: Dynamic loading of provider-specific components
- **Configuration Validation**: Real-time validation with error messages
- **Fallback Components**: Default settings for unknown providers

## Testing Checklist

### Backend Integration
- [ ] Provider registry loads successfully from `/api/llm/providers`
- [ ] Provider info retrieved for each type
- [ ] Configuration validation works
- [ ] Health checks and model listing endpoints respond

### Frontend Components
- [ ] BaseProviderSettings renders correctly
- [ ] API key fields conditionally shown/hidden
- [ ] CLI mode toggles switch between TUI/headless views
- [ ] Headless templates include `{model}` and `{prompt}`
- [ ] Token usage appears in test results when available

### User Experience
- [ ] Provider selection dropdown works
- [ ] Add provider button creates new provider
- [ ] Edit mode shows provider-specific settings
- [ ] Navigation between views works smoothly

## Integration Points

### 1. Parent Component Integration
```typescript
// In your main LLM settings component
import { LLMSettingsTab } from './LLMSettingsTab';

<LLMSettingsTab
  llmConfig={llmConfig}
  llmStatus={llmStatus}
  llmLoading={llmLoading}
  llmSaving={llmSaving}
  llmError={llmError}
  onSaveConfig={handleSaveConfig}
  onRunInterview={handleRunInterview}
  onRunReadiness={handleRunReadiness}
  onAddProvider={handleAddProvider}
  onUpdateProvider={handleUpdateProvider}
  onDeleteProvider={handleDeleteProvider}
/>
```

### 2. Backend API Endpoints Required
- `GET /api/llm/providers` - List all providers
- `GET /api/llm/providers/{type}/info` - Get provider info
- `GET /api/llm/providers/{type}/config` - Get default config
- `POST /api/llm/providers/{type}/validate` - Validate config
- `POST /api/llm/providers/{provider_id}/health` - Health check
- `POST /api/llm/providers/{provider_id}/models` - List models

### 3. State Management
The enhanced component expects these parent handlers:
- `onAddProvider(providerId, provider)` - Add new provider
- `onUpdateProvider(providerId, updates)` - Update provider
- `onDeleteProvider(providerId)` - Delete provider

## Known Issues & Solutions

### 1. Module Resolution Errors
**Issue**: TypeScript can't find provider modules  
**Solution**: Ensure all provider components are exported correctly in their files

### 2. Async Validation
**Issue**: Validation function returns Promise  
**Solution**: Handle validation in parent component or use mock validation for demo

### 3. API Integration
**Issue**: Backend endpoints may not be implemented yet  
**Solution**: Use mock data or implement backend endpoints first

## Next Steps

1. **Complete Backend Integration**: Ensure all API endpoints are implemented
2. **Add Error Handling**: Improve error messages and fallback states
3. **Add Loading States**: Better loading indicators for async operations
4. **Implement Real Validation**: Connect validation to backend API
5. **Expand Token Usage**: Surface token usage in more views when available

## Success Criteria

✅ **Conditional API Key Display**: Users only see API key fields when needed  
✅ **Provider-Specific Settings**: Each provider shows relevant configuration options  
✅ **CLI Modes**: Users can pick TUI or headless mode for CLI providers  
✅ **Token Usage Visibility**: Token metrics are shown without extra UI  
✅ **Dynamic Loading**: Providers load automatically without hardcoding  
✅ **User Experience**: Intuitive interface with clear navigation

The enhanced LLM provider system focuses on token usage and CLI execution modes while keeping configuration simple and provider-specific.
