# Qwen3-Coder 文本工具调用恢复蓝图 (2026-06-16)

## 0. 一句话

绑定 Director 的 qwen3.6-27b-**code** 变体在复杂上下文里把工具调用以
`<function=NAME><parameter=KEY>VALUE</parameter></function>`(Qwen3-Coder/qwen-agent 等号式)
文本形式留在 `content` 里(当远端 vLLM 的 tool-call-parser 未把它转成原生 `tool_calls[]` 时,概率性发生),
而 Polaris 现有的所有解析器**只认 `name="..."` 属性式**,等号式一个都不匹配 → Director 解码得到空工具调用
→ 被当作散文 → mutation 契约违约 → dead-letter(`bootstrap follow-up did not produce tool batch`)。

## 1. 证据(已实测,非推测)

`KERNELONE_TC_TRACE` 注入三处(caller / invoker / wire)后 replay L2-12@zuoceA(code-gpu0):
- **强制 tool_choice 正确到达线上**:`[wire-nonstream] n_tools=2 tool_choice={'type':'function','function':{'name':'write_file'}}`
  → 推翻"tool_choice 被吞"的旧假设;text-fallback **从未触发**(FALLBACK fired: 0)。
- **Director 走非流式** `native_tool_mode=native_tools`。
- **code 变体实际输出**(chain.log 行 35):
  ```
  <function=write_file>
  <parameter=file>
  README.md
  </parameter>
  <parameter=content>
  # 项目脚手架 ...
  </parameter>
  ```
  原生 `tool_calls[]` 为空,等号式文本落在 `content`。
- 同一 backend 在**短 prompt + 强制 tool_choice** 下(curl)会返回干净的原生 `tool_calls`
  → 故远端 vLLM 的转换是**不稳定**的(有时转、有时漏),漏的那次就击穿。
- 该 run 最终 PASS(6 文件):说明大多数 turn 被远端转成了原生调用,只有漏出的等号式文本害死个别 turn。

## 2. 现状缺口(两处)

1. **无任何解析器识别等号式** `<function=NAME>` / `<parameter=KEY>`:
   `xml_based.py` 的 `FUNCTION_PATTERN`/`BAICHUAN_PARAM_PATTERN` 等全部要求 `name="..."` 属性式。
2. **`XMLToolParser` 未接入非流式 Director 解码**(`output_parser.parse_execution_tool_calls`):
   该路径只有 native → JSON-content → `recover_textual_tool_calls`(textual)三层,
   而 textual 层只认 `call:NAME{...}` / pythonic / LFM,不认等号式 XML。流式路径(stream/executor.py:917)
   才用 `XMLToolParser`。

## 3. 文本架构

```
zuoce vLLM (qwen3-coder) ──content: "<function=write_file>...<parameter=...>...</parameter>"──┐
                                                                                              ▼
RawLLMResponse(content=…, tool_calls=[])                                                      │
        │                                                                                     │
        ▼ 非流式 Director 解码                                                                  │
output_parser.parse_execution_tool_calls                                                      │
  Layer1 native tool_calls[]      → 空                                                         │
  Layer2 JSON content             → 不匹配等号式                                               │
  Layer3 has_textual_tool_calls ──(本蓝图扩展)──► 新增识别 "<function="                        │
         └► recover_textual_tool_calls ──(本蓝图扩展)──► _iter_qwen3coder_xml_calls ◄──────────┘
                                                          → {"tool":"write_file","arguments":{file,content}}
流式 Director 解码 stream/executor.py:917 XMLToolParser ──(本蓝图扩展)──► 等号式 pattern
```

## 4. 模块职责与改动

### 4.1 `kernelone/llm/toolkit/parsers/textual_tool_recovery.py`(主修,已接入活路径)
- 新增 `_iter_qwen3coder_xml_calls(text) -> [(start,end,tool_name,args)]`:
  - `<function\s*=\s*(NAME)\s*>` 起;体到 `</function>` / 下一个 `<function=` / 文末(截断容错)止。
  - 体内 `<parameter\s*=\s*(KEY)\s*>(.*?)</parameter>`(DOTALL)逐个取,`value.strip()`(去格式外包的换行,
    保留代码内部缩进);顶层代码不以有意义空白起,故 strip 安全。
  - 无任何闭合 `<parameter>`(截断在值中途)→ 跳过,交给强制写 re-ask,不做部分猜测。
- `_iter_textual_calls` 末尾 `results.extend(_iter_qwen3coder_xml_calls(text))`,统一进 sorted。
  → `recover_textual_tool_calls` 与 `strip_textual_tool_call_markers` 同时获益。
- `has_textual_tool_calls` 增加 `_QWEN3CODER_FUNCTION_OPEN_RE.search(token)` 短路。
- **假阳防护**:`recover_textual_tool_calls` 已用 `allowed_tool_names` 过滤(只恢复白名单工具名),
  且要求至少一个闭合 `<parameter>`;散文里偶现 `<function=foo>` 不在白名单 → 不恢复。

### 4.2 `kernelone/llm/toolkit/parsers/xml_based.py`(流式路径防御纵深)
- 在 `XMLToolParser` 增加等号式 `FUNCTION_EQ_PATTERN`/`PARAM_EQ_PATTERN`,使 stream/executor.py:917 也覆盖。

### 4.3 不改 `output_parser.py`
- Layer 3 已调用扩展后的两个函数 → 非流式 Director 自动获益,零额外接线。

## 5. 核心数据流(成功路径)

`content "<function=write_file><parameter=file>README.md</parameter><parameter=content>…</parameter>"`
→ `has_textual_tool_calls`=True → `recover_textual_tool_calls`(allowed={write_file,…})
→ `[{"tool":"write_file","arguments":{"file":"README.md","content":"…"}}]`
→ TurnDecision=TOOL_BATCH → 落盘 → 不再 dead-letter。

## 6. 技术理由

- 受 `normalize-toolcalls-adapt-to-llm` 铁律(我方归一化去适应 LLM,而非逼模型)与 Task #50 指引。
- 远端 model_id 写死、不可改 → 只能 harness 侧适配。
- 改在**解码兼容层**、`allowed_tool_names` 严格门控 → 不污染审计、不破坏 §6.6 单一身份
  (这是恢复 content 里的真实调用为规范名,不是改写 raw 名;raw content 保真)。
- 与 [[forced-toolchoice-dropped-before-wire]] 的修正:tool_choice 到达线上无误,真因是输出格式。

## 7. 验证

1. 单测:`test_textual_tool_recovery.py` 增等号式用例(单调用/多调用/多参数/截断/白名单过滤/含代码内容/`<tool_call>`包裹);
   `xml_based` 等号式用例。
2. 门禁:ruff check --fix + ruff format + mypy + pytest(相关测试 + 回归)。
3. 实战:移除 TC_TRACE 探针后 replay L2-12/L2-09@zuoce,确认等号式被恢复、dead-letter 归零。

## 8. 风险与边界

- 仅恢复**白名单内**工具名;真·散文不受影响。
- 截断在值中途 → 不部分恢复(避免落半个文件);由既有强制写 re-ask 兜底。
- 不解决 line-133 纯散文(模型连等号式都没发)那种概率性不合作 → 由既有重试/read-loop 兜底,非本蓝图目标。
- value `.strip()` 对顶层代码安全;若未来出现首行有意义缩进的参数值,再引入"仅剥格式换行"的精细 strip。
