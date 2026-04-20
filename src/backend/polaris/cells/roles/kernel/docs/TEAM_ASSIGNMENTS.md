# 10人团队详细任务分配

---

## Team Alpha - LLM调用层修复

### 成员
- **队长**: Senior Backend Engineer (Alex)
- **成员**: Backend Engineer (Bob)

### 负责范围
`polaris/cells/roles/kernel/tests/test_llm_caller.py` - 48个失败测试

### 失败测试清单

#### 1. Timeout配置测试 (4 tests)
- [ ] `test_director_role_gets_600_seconds`
- [ ] `test_non_director_role_gets_60_seconds`
- [ ] `test_director_role_respects_env_override`
- [ ] `test_timeout_clamped_to_max_900`

**根因**: `_resolve_timeout_seconds` 函数重构后逻辑变更
**修复策略**: 更新timeout解析逻辑，确保director角色600s，其他60s

#### 2. 重试配置测试 (3 tests)
- [ ] `test_director_role_returns_zero`
- [ ] `test_non_director_role_returns_requested`
- [ ] `test_non_director_role_handles_invalid`

**根因**: `_resolve_platform_retry_max` 实现变更
**修复策略**: 确保director不重试，其他角色按配置

#### 3. Provider解析测试 (3 tests)
- [ ] `test_anthropic_keywords_resolve_to_anthropic`
- [ ] `test_openai_keywords_resolve_to_openai`
- [ ] `test_empty_returns_auto`

**根因**: `_resolve_tool_call_provider` 逻辑变更
**修复策略**: 关键字映射到正确provider

#### 4. Native Tool Calling不支持检测 (5 tests)
- [ ] `test_unsupported_parameter_detected`
- [ ] `test_tools_not_allowed_detected`
- [ ] `test_function_calling_not_supported_detected`
- [ ] `test_invalid_tools_bad_request_detected`
- [ ] `test_empty_returns_false`
- [ ] `test_normal_error_returns_false`

**根因**: `_is_native_tool_calling_unsupported` 实现变更
**修复策略**: 正确识别不支持native tool calling的情况

#### 5. Schema构建测试 (2 tests)
- [ ] `test_builds_repo_contract_schema_when_registry_missing`
- [ ] `test_repo_read_head_schema_exposes_alias_params`

**根因**: `_build_native_tool_schemas` 使用新ToolSpecRegistry
**修复策略**: 更新schema构建逻辑

#### 6. Native Tool Calls提取 (4 tests)
- [ ] `test_extracts_openai_tool_calls_from_top_level`
- [ ] `test_extracts_openai_tool_calls_from_choices`
- [ ] `test_extracts_anthropic_tool_use_blocks`
- [ ] `test_empty_payload_returns_empty`
- [ ] `test_non_dict_returns_empty`

**根因**: `_extract_native_tool_calls` 函数重构
**修复策略**: 正确提取OpenAI/Anthropic工具调用

#### 7. JSON提取测试 (6 tests)
- [ ] `test_extracts_json_from_fenced_block`
- [ ] `test_extracts_json_array_from_fenced_block`
- [ ] `test_extracts_bare_json`
- [ ] `test_empty_text_raises`
- [ ] `test_whitespace_only_raises`
- [ ] `test_no_valid_json_raises`

**根因**: `_extract_json_from_text` 移至新模块
**修复策略**: 更新导入和调用

#### 8. 错误分类测试 (9 tests)
- [ ] `test_timeout_classification`
- [ ] `test_rate_limit_classification`
- [ ] `test_network_classification`
- [ ] ... (其他6个)

**根因**: `_classify_error` 错误分类逻辑变更
**修复策略**: 确保错误分类一致

#### 9. 生命周期和缓存测试 (12 tests - 2 errors)
- [ ] `test_call_response_format_fallback_uses_chat_mode_builder`
- [ ] `test_call_structured_fallback_non_ok_response_keeps_error_category`

**根因**: 新LLMInvoker接口变更
**修复策略**: 更新测试Mock

### 交付检查清单
- [ ] 48个测试全部通过
- [ ] 新增/修改代码100%类型注解
- [ ] Ruff零警告
- [ ] 更新LLM调用架构文档

---

## Team Beta - 流式工具循环修复

### 成员
- **队长**: Senior Backend Engineer (Carol)
- **成员**: Backend Engineer (David)

### 负责范围
- `test_kernel_stream_tool_loop.py` - 16 tests
- `test_integration_transactional_flow.py` - 1 test

### 失败测试清单

#### 1. 工具结果后继续 (2 tests)
- [ ] `test_stream_continues_after_tool_results_with_transcript_context`
- [ ] `test_run_continues_after_tool_results_with_transcript_context`

**根因**: TurnEngine工具循环控制器状态传递
**修复策略**: 确保工具结果正确追加到transcript

#### 2. Native Tool Calls执行 (2 tests)
- [ ] `test_stream_executes_native_tool_calls_without_text_wrapper`
- [ ] `test_stream_executes_normalized_tool_calls_even_with_anthropic_provider_metadata`

**根因**: Native tool calls解析和执行路径
**修复策略**: 统一native/textual工具调用处理

#### 3. 重复循环安全 (2 tests)
- [ ] `test_stream_repeated_identical_tool_cycle_emits_safety_error`
- [ ] `test_run_repeated_identical_tool_cycle_emits_safety_error`

**根因**: ToolLoopController循环检测
**修复策略**: 正确检测和阻止重复工具调用

#### 4. 工具结果压缩 (2 tests)
- [ ] `test_stream_compacts_large_tool_receipts_in_transcript`
- [ ] `test_stream_keeps_read_file_receipt_when_context_budget_allows`

**根因**: Context compaction触发条件
**修复策略**: 调整压缩阈值和策略

#### 5. 代码块示例过滤 (2 tests)
- [ ] `test_stream_examples_inside_code_blocks_do_not_execute`
- [ ] `test_run_examples_inside_code_blocks_do_not_execute`

**根因**: 代码块内工具调用识别
**修复策略**: 正确识别和过滤示例代码

#### 6. 空/Thinking响应处理 (4 tests)
- [ ] `test_stream_thinking_only_response_emits_explicit_error`
- [ ] `test_stream_blank_response_emits_explicit_error`
- [ ] `test_run_thinking_only_response_returns_explicit_error`
- [ ] `test_run_blank_response_returns_explicit_error`

**根因**: 响应内容验证逻辑
**修复策略**: 正确识别和处理无效响应

#### 7. 工具调用去重 (2 tests)
- [ ] `test_stream_dedupes_identical_parsed_tool_calls_within_same_round`
- [ ] `test_run_dedupes_identical_parsed_tool_calls_within_same_round`

**根因**: 同一轮次重复工具调用检测
**修复策略**: 添加去重逻辑

#### 8. 事务流集成 (1 test)
- [ ] `test_final_answer_full_flow`

**根因**: 完整turn执行流程
**修复策略**: 确保端到端流程正确

### 交付检查清单
- [ ] 17个测试全部通过
- [ ] Stream/Run行为一致
- [ ] 工具循环边界情况处理完善

---

## Team Gamma - Stream/Non-stream一致性

### 成员
- **队长**: Senior Backend Engineer (Eve)
- **成员**: Backend Engineer (Frank)

### 负责范围
- `test_run_stream_parity.py` - 7 tests
- `test_stream_parity.py` - 8 tests

### 失败测试清单

#### 1. 基本内容一致性 (2 tests)
- [ ] `test_run_and_stream_produce_equivalent_content`
- [ ] `test_same_input_produces_same_content`

**根因**: Stream/Non-stream输出格式差异
**修复策略**: 实现Stream-First架构

#### 2. Transcript一致性 (2 tests)
- [ ] `test_run_and_stream_accumulate_identical_transcript`
- [ ] `test_stream_non_stream_produce_same_transcript`

**根因**: 历史记录累积差异
**修复策略**: 统一transcript构建

#### 3. 元数据一致性 (1 test)
- [ ] `test_run_and_stream_emit_turn_envelope_metadata`

**根因**: 事件元数据格式差异
**修复策略**: 统一元数据结构

#### 4. 工具结果一致性 (2 tests)
- [ ] `test_run_and_stream_produce_equivalent_tool_results`
- [ ] `test_stream_emits_tool_call_events`

**根因**: 工具调用序列差异
**修复策略**: 统一工具执行顺序

#### 5. 停滞检测一致性 (2 tests)
- [ ] `test_run_and_stream_stall_detection_parity`
- [ ] `test_stream_history_correctly_passed_between_rounds`

**根因**: 停滞检测逻辑差异
**修复策略**: 统一停滞检测

#### 6. 多轮对话历史 (3 tests)
- [ ] `test_history_correctly_passed_between_rounds`
- [ ] `test_multi_round_conversation_persists_context`
- [ ] `test_stream_history_correctly_passed_between_rounds`

**根因**: 历史传递机制差异
**修复策略**: 统一history管理

#### 7. 错误处理一致性 (2 tests)
- [ ] `test_run_and_stream_produce_equivalent_errors`
- [ ] `test_same_error_handling_behavior`

**根因**: 错误处理路径差异
**修复策略**: 统一错误处理

#### 8. 截断一致性 (1 test)
- [ ] `test_run_and_stream_truncate_large_content_identically`

**根因**: 内容截断逻辑差异
**修复策略**: 统一截断策略

### 架构目标
实现 **Stream-First Architecture**:
- `run()` 是 `run_stream()` 的包装器
- 使用 `async for event in self.run_stream():` 模式收集结果
- 确保行为完全一致

### 交付检查清单
- [ ] 15个测试全部通过
- [ ] Stream-First架构实现
- [ ] 性能不下降

---

## Team Delta - TurnEngine核心修复

### 成员
- **队长**: Senior Backend Engineer (Grace)
- **成员**: Backend Engineer (Henry)

### 负责范围
- `test_turn_engine_semantic_stages.py` - 10 tests
- `test_turn_engine_enrichment.py`
- `test_turn_engine_event_contract.py`
- `test_turn_engine_thinking_persistence.py`

### 失败测试清单

#### 1. 语义阶段处理 (10 tests)
- [ ] `test_materialize_assistant_turn_keeps_raw_wrapper_but_sanitizes_output`
- [ ] `test_materialize_assistant_turn_strips_output_wrappers_from_raw_and_clean_content`
- [ ] `test_clean_content_strips_multiple_interleaved_tool_wrappers`
- [ ] `test_clean_content_empty_when_raw_is_only_tool_wrapper`
- [ ] `test_sanitize_strips_variations_of_canonical_wrappers`
- [ ] `test_clean_content_is_used_for_parser_in_parse_tool_calls`
- [ ] `test_quoted_tool_wrapper_not_stripped_from_clean_content`
- [ ] `test_native_tool_calls_suppress_textual_fallback`
- [ ] `test_parse_tool_calls_from_turn_uses_clean_content_contract`
- [ ] `test_thinking_with_tool_wrapper_does_not_leak_into_clean_content`

**根因**: TurnEngine语义阶段处理逻辑变更
**修复策略**: 统一内容清洗和工具解析

### 交付检查清单
- [ ] 35个测试全部通过
- [ ] TurnEngine语义阶段文档更新

---

## Team Epsilon - Context压缩与安全

### 成员
- **队长**: Backend Engineer (Ivy)

### 负责范围
- `test_transcript_leak_guard.py` - 3 tests
- `test_turn_engine_policy_convergence.py` - 2 tests

### 失败测试清单

#### 1. Context压缩 (3 tests)
- [ ] `test_context_gateway_apply_compression_truncates_not_corrupts`
- [ ] `test_summarize_strategy_emits_continuity_summary_message`
- [ ] `test_build_context_compaction_triggered_skips_compression`

**根因**: ContextOverflowError异常抛出 vs 静默处理
**修复策略**: 调整压缩策略，确保测试期望的行为

#### 2. 策略收敛 (2 tests)
- [ ] `test_run_single_failed_tool_cycle_does_not_trigger_stall`
- [ ] `test_run_stream_single_failed_tool_cycle_does_not_trigger_stall`

**根因**: 单次失败不应触发停滞检测
**修复策略**: 修复停滞检测逻辑

### 交付检查清单
- [ ] 5个测试全部通过
- [ ] 压缩算法优化文档

---

## Team Zeta - 兼容性方法修复

### 成员
- **队长**: Backend Engineer (Jack)

### 负责范围
- `test_turn_engine_compat_methods.py` - 2 tests
- `test_kernel_prompt_builder_integration.py` - 1 test

### 失败测试清单

- [ ] `test_turn_engine_compat_methods_are_runnable`
- [ ] `test_turn_engine_maybe_compact_triggers_under_pressure`
- [ ] `test_build_system_prompt_for_request_passes_message_to_prompt_builder`

**根因**: 向后兼容方法接口变更
**修复策略**: 更新兼容层或测试

### 交付检查清单
- [ ] 3个测试全部通过
- [ ] 兼容性文档

---

## Team Eta - 事务控制器修复

### 成员
- **队长**: Backend Engineer (Kate)

### 负责范围
- `test_transaction_controller.py` - 5 tests
- `test_regression_kernel_context.py` - 2 tests

### 失败测试清单

- [ ] `test_final_answer_turn`
- [ ] `test_handoff_triggered_by_async_tool`
- [ ] `test_handoff_triggered_by_many_tools`
- [ ] `test_tool_failure_continues`
- [ ] `test_workflow_handoff_persists_state`
- [ ] `test_context_preserves_tool_results`
- [ ] `test_context_handles_empty_history`

**根因**: 事务流程和工作流交接逻辑
**修复策略**: 修复事务控制器状态管理

### 交付检查清单
- [ ] 7个测试全部通过

---

## Team Theta - 流式输出契约

### 成员
- **队长**: Backend Engineer (Leo)

### 负责范围
- `test_stream_visible_output_contract.py` - 8 tests
- `test_turn_phase_renderer.py` - 4 tests

### 失败测试清单

- [ ] `test_stream_emits_only_sanitized_visible_content`
- [ ] `test_stream_preserves_provider_reasoning_without_content_leak`
- [ ] `test_stream_emits_incremental_visible_deltas`
- [ ] `test_stream_avoids_per_chunk_full_rematerialization`
- [ ] `test_stream_strips_split_bracket_tool_wrappers`
- [ ] `test_stream_strips_output_wrappers_from_visible_content`
- [ ] `test_stream_handles_nested_function_calls_without_leaking_closing_tags`
- [ ] `test_stream_strips_tool_wrappers_from_visible_content`
- [ ] `test_format_ledger`
- [ ] `test_renders_visible_content`
- [ ] `test_hides_thinking_by_default`
- [ ] `test_shows_thinking_when_enabled`

**根因**: 可见内容净化和格式化
**修复策略**: 更新输出过滤器

### 交付检查清单
- [ ] 12个测试全部通过

---

## Team Iota - 解析器与指标

### 成员
- **队长**: Backend Engineer (Mia)

### 负责范围
- `test_pydantic_output_parser.py` - 4 tests
- `test_llm_caller_text_fallback.py` - 3 tests
- `test_metrics.py` - 7 tests
- 其他 - 1 test

### 失败测试清单

#### 1. Pydantic解析器 (4 tests)
- [ ] `test_parse_with_fallback_extracts_json_when_possible`
- [ ] `test_parse_triple_backtick_variants`
- [ ] `test_fallback_schema_returns_generic_role_response`

#### 2. 文本回退 (3 tests)
- [ ] `test_invalid_tool_name`
- [ ] `test_missing_name`
- [ ] `test_non_dict_input`

#### 3. 指标 (7 tests)
- [ ] `test_counter_with_labels`
- [ ] `test_counter_collect`
- [ ] `test_metrics_collector_record_execution`
- [ ] `test_metrics_collector_record_retry`
- [ ] `test_metrics_collector_record_cache_hit`
- [ ] `test_metrics_collector_record_cache_miss`
- [ ] `test_record_cache_stats`

**根因**: 各类小模块接口变更
**修复策略**: 个案修复

### 交付检查清单
- [ ] 15个测试全部通过

---

# 汇总

| 团队 | 人数 | 测试数 | 预计时间 |
|------|------|--------|----------|
| Alpha | 2 | 48 | 3天 |
| Beta | 2 | 17 | 3天 |
| Gamma | 2 | 15 | 4天 |
| Delta | 2 | 35 | 3天 |
| Epsilon | 1 | 5 | 2天 |
| Zeta | 1 | 3 | 1天 |
| Eta | 1 | 7 | 2天 |
| Theta | 1 | 12 | 2天 |
| Iota | 1 | 15 | 2天 |
| **总计** | **13** | **167** | **4天并行** |

*注: 实际10人，其中4个团队各2人，5个团队各1人*
