# ADR-0076: ContextOS 2.0 摘要策略选型

**状态**: 提议 (Proposed)  
**日期**: 2026-04-11  
**作者**: ContextOS 2.0 架构团队  
**相关**: ADR-0066 (Benchmark Framework Convergence), CONTEXTOS_2.0_BLUEPRINT

---

## 背景

ContextOS 在处理长上下文时面临严重的 token 预算压力。当前实现使用简单的截断策略，导致：

1. **关键信息丢失**: 错误堆栈被截断，LLM 无法诊断问题
2. **幻觉风险**: 粗暴截断可能破坏代码结构，导致 LLM 产生错误推断
3. **重复读取**: 由于信息不可见，LLM 反复请求相同文件

需要引入专业的摘要生成策略，在保证信息完整性的同时控制 token 使用量。

---

## 决策

### 总体策略: 分层摘要架构 (Tiered Summarization)

采用三层的摘要策略，根据内容类型、预算压力和系统负载动态选择实现。

```
┌─────────────────────────────────────────────────────────────┐
│                    Tier 1: 智能摘要层                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ transformers │  │   LangChain  │  │  LLMLingua      │   │
│  │ (BART/Qwen)  │  │  Map-Reduce  │  │ (Prompt压缩)    │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    Tier 2: 安全摘要层                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │    sumy      │  │  Tree-sitter │  │   Code Folding  │   │
│  │ (TextRank)   │  │   (AST结构)   │  │   (代码折叠)     │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    Tier 3: 紧急回退层                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │  首尾截断     │  │   签名保留    │  │    哈希替换      │   │
│  │ (Head/Tail)  │  │ (Signatures) │  │  (Content Hash) │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 方案详细对比

### 方案 A: 生成式摘要 (Abstractive)

**技术选型**: Hugging Face transformers (BART/Qwen-1.8B)

**适用场景**:
- 对话历史压缩 (去除口语废话)
- 长文档意图提炼
- 多轮上下文合并

**优势**:
- 生成流畅、类似人类的摘要
- 可理解语义并用自己的话重写
- 支持多语言 (Qwen-1.8B 中文效果优秀)

**劣势**:
- **幻觉风险**: 可能篡改技术细节 (行号、变量名、错误信息)
- GPU 依赖: 无 GPU 时 CPU 推理慢
- Token 限制: 模型本身有 max_length 限制

**代码示例**:
```python
from transformers import pipeline

summarizer = pipeline(
    "summarization",
    model="Qwen/Qwen1.5-1.8B-Chat",  # 轻量级中文模型
    device=-1,  # CPU fallback
)

summary = summarizer(
    long_dialogue,
    max_length=130,
    min_length=30,
    do_sample=False,  # 确定性输出
)
```

**ContextOS 集成点**:
- Phase 3: 作为 `SemanticSummarizer` 实现
- 仅用于 `dialogue` 类型的内容
- 需要显式开启 (环境变量 `CONTEXTOS_ENABLE_GENERATIVE_SUMMARY=1`)

---

### 方案 B: 编排式摘要 (Orchestrated)

**技术选型**: LangChain Map-Reduce / LlamaIndex

**适用场景**:
- 超长文本 (>10K tokens)
- 多源内容合并
- 需要保持跨段落关联的复杂文档

**工作原理**:
```
Input (100K tokens)
    │
    ▼
┌─────────────┐
│  Splitter   │ → 10 chunks × 10K tokens
└─────────────┘
    │
    ▼
┌─────────────┐
│  Map Phase  │ → 10 sub-summaries (并行)
└─────────────┘
    │
    ▼
┌─────────────┐
│ Reduce Phase│ → Final summary
└─────────────┘
```

**优势**:
- 解决超长文本的内存和注意力截断问题
- 可接入本地模型 (Llama-3) 或云端 API
- 模块化设计，易于替换底层模型

**劣势**:
- 引入 LangChain 依赖 (约 50MB)
- 多次 LLM 调用增加延迟
- 需要精细的 prompt 工程保证一致性

**ContextOS 集成点**:
- Phase 4: 作为 `LongContextSummarizer` 实现
- 仅在 `budget_pressure="emergency"` 时启用
- 依赖 `LLMInvokerPort` 抽象，不直接绑定 LangChain

---

### 方案 C: 抽取式摘要 (Extractive) ⭐ **首选基线**

**技术选型**: sumy (TextRank/LexRank)

**适用场景**:
- 系统日志/错误堆栈
- 代码片段提取
- API 返回结果
- 任何需要**精确保真**的技术内容

**优势**:
- **零幻觉**: 直接从原文抽取句子，不会篡改
- **极速**: CPU 上毫秒级响应
- **轻量**: 纯 Python，无 ML 依赖
- **安全**: 适合处理不可信的日志/错误信息

**劣势**:
- 生成质量受限于原文质量
- 无法合并多个句子的信息
- 对口语化文本效果差

**代码示例**:
```python
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.text_rank import TextRankSummarizer

def summarize_with_sumy(text: str, sentence_count: int = 3) -> str:
    parser = PlaintextParser.from_string(text, Tokenizer("chinese"))
    summarizer = TextRankSummarizer()
    summary = summarizer(parser.document, sentence_count)
    return "\n".join(str(s) for s in summary)
```

**ContextOS 集成点**:
- Phase 2: 作为默认 `SummarizerInterface` 实现
- 用于所有 `tool_result` 类型的内容
- 内置关键词增强 (优先选择包含 "Error", "Exception" 的句子)

---

### 方案 D: 结构化压缩 (Structured)

**技术选型**: Tree-sitter + AST 感知

**适用场景**:
- 代码文件 (>100 行)
- JSON/XML 配置文件
- 结构化日志

**策略**:
```python
CODE_COMPRESSION_STRATEGIES = {
    "signatures_only": "保留类/函数签名，折叠实现体",
    "docstring_focus": "保留文档字符串和类型注解",
    "error_paths": "保留错误处理分支，折叠正常路径",
}
```

**Tree-sitter 示例**:
```python
from tree_sitter import Language, Parser
import tree_sitter_python as tspython

parser = Parser(Language(tspython.language()))

def compress_code(code: str, strategy: str = "signatures_only") -> str:
    tree = parser.parse(code.encode())
    root = tree.root_node

    compressed_lines = []
    for child in root.children:
        if child.type == "function_definition":
            # 提取函数签名
            signature = code[child.start_byte:child.children[0].end_byte]
            compressed_lines.append(f"{signature} ...")
        elif child.type == "class_definition":
            class_name = extract_class_name(child)
            compressed_lines.append(f"class {class_name}: ...")
        else:
            compressed_lines.append(code[child.start_byte:child.end_byte])

    return "\n".join(compressed_lines)
```

**ContextOS 集成点**:
- Phase 3: 作为 `CodeSummarizer` 实现
- 在 `RoutingClass.SUMMARIZE` 路由决策后调用
- 根据 `mime_type` 自动选择语言 parser

---

## 防腐层设计 (Anti-Corruption Layer)

统一接口，支持运行时降级:

```python
# polaris/kernelone/context/context_os/summarizers/contracts.py
from typing import Protocol, runtime_checkable
from enum import Enum, auto

class SummaryStrategy(Enum):
    EXTRACTIVE = auto()      # sumy
    GENERATIVE = auto()      # transformers
    STRUCTURED = auto()      # tree-sitter
    ORCHESTRATED = auto()    # langchain
    TRUNCATION = auto()      # emergency fallback

@runtime_checkable
class SummarizerInterface(Protocol):
    """摘要器统一接口"""

    strategy: SummaryStrategy

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """
        Args:
            content: 原始内容
            max_tokens: 目标 token 数 (不是硬截断，是指导值)
            content_type: 内容类型 (text/code/log/json)

        Returns:
            摘要后的内容

        Raises:
            SummarizationError: 当无法生成满足要求的摘要时
        """
        ...

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数，用于预算规划"""
        ...
```

### 分层降级策略

```python
class TieredSummarizer:
    """
    三层降级策略:
    1. 首选: 根据内容类型选择最优策略
    2. 降级: 首选失败时切换到更轻量的策略
    3. 紧急: 所有策略失败时使用截断
    """

    STRATEGY_CHAIN: dict[str, list[SummaryStrategy]] = {
        "dialogue": [
            SummaryStrategy.GENERATIVE,   # BART/Qwen
            SummaryStrategy.EXTRACTIVE,   # sumy
            SummaryStrategy.TRUNCATION,
        ],
        "code": [
            SummaryStrategy.STRUCTURED,   # tree-sitter
            SummaryStrategy.EXTRACTIVE,   # sumy on docstrings
            SummaryStrategy.TRUNCATION,
        ],
        "log": [
            SummaryStrategy.EXTRACTIVE,   # sumy (error keywords)
            SummaryStrategy.TRUNCATION,
        ],
        "default": [
            SummaryStrategy.EXTRACTIVE,
            SummaryStrategy.TRUNCATION,
        ],
    }

    def __init__(self):
        self._summarizers: dict[SummaryStrategy, SummarizerInterface] = {
            SummaryStrategy.EXTRACTIVE: SumySummarizer(),
            SummaryStrategy.STRUCTURED: TreeSitterSummarizer(),
            SummaryStrategy.GENERATIVE: TransformersSummarizer(),
            SummaryStrategy.TRUNCATION: TruncationSummarizer(),
        }
        self._fallback_stats: dict[SummaryStrategy, int] = defaultdict(int)

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        strategies = self.STRATEGY_CHAIN.get(content_type, self.STRATEGY_CHAIN["default"])

        for strategy in strategies:
            summarizer = self._summarizers.get(strategy)
            if summarizer is None:
                continue

            try:
                result = summarizer.summarize(content, max_tokens, content_type)
                # 验证结果质量
                if self._validate_result(result, content, max_tokens):
                    return result
            except Exception as e:
                logger.warning(f"Summarizer {strategy.name} failed: {e}")
                self._fallback_stats[strategy] += 1
                continue

        # 绝对 fallback: 硬截断
        return content[:max_tokens * 4]  # 估算: 1 token ≈ 4 chars

    def _validate_result(self, result: str, original: str, max_tokens: int) -> bool:
        """验证摘要结果是否有效"""
        if not result or len(result) < 10:
            return False
        # 确保没有丢失关键信息 (如 Error 关键字)
        critical_keywords = ["error", "exception", "failed"]
        original_has_critical = any(kw in original.lower() for kw in critical_keywords)
        result_has_critical = any(kw in result.lower() for kw in critical_keywords)
        if original_has_critical and not result_has_critical:
            return False
        return True
```

---

## 决策矩阵

| 内容类型 | 首选策略 | 降级策略 | 关键指标 |
|----------|----------|----------|----------|
| 对话历史 | transformers | sumy | ROUGE-L > 0.3 |
| 代码文件 | tree-sitter | sumy | 签名保留率 100% |
| 错误日志 | sumy | truncation | 错误关键字保留率 100% |
| JSON/XML | tree-sitter | truncation | 结构完整性 |
| 长文档 | langchain | sumy | 信息覆盖率 > 80% |

---

## 实施路线图

### Phase 2 (Week 1-2): 抽取式基线

- [ ] 引入 `sumy` 依赖
- [ ] 实现 `SumySummarizer`
- [ ] 集成到 `Canonicalizer` 的 `RoutingClass.SUMMARIZE` 路径
- [ ] 添加关键词增强 (Error/Exception 优先保留)

### Phase 3 (Week 3-4): 结构化压缩

- [ ] 引入 `tree-sitter` 依赖
- [ ] 实现 `TreeSitterSummarizer` (Python, JavaScript)
- [ ] 实现代码折叠策略
- [ ] 集成到 `ArtifactSelector` 的代码路径

### Phase 4 (Week 5-6): 生成式增强 (可选)

- [ ] 评估 `transformers` 性能影响
- [ ] 实现 `TransformersSummarizer` (异步，带超时)
- [ ] 实现 `LangChainSummarizer` (超长文档)
- [ ] 配置化管理 (feature flags)

---

## 监控与指标

在 `ContextOSMetricsCollector` 中增加:

```python
@dataclass
class SummarizationMetrics:
    total_requests: int
    strategy_usage: dict[SummaryStrategy, int]
    fallback_count: dict[SummaryStrategy, int]
    avg_latency_ms: dict[SummaryStrategy, float]
    quality_score: float  # 基于关键词保留率计算
```

---

## 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| sumy 对中文支持不佳 | 中 | 高 | 使用 jieba 分词增强 |
| transformers 引入大依赖 | 高 | 中 | 设为可选依赖 (extras_require) |
| tree-sitter 语法解析失败 | 中 | 低 | fallback 到 sumy |
| 降级链全部失败 | 低 | 高 | 绝对 fallback 到 truncation |

---

## 相关决策

- **ADR-0066**: Benchmark Framework Convergence (评估指标)
- **CONTEXTOS_2.0_BLUEPRINT**: Phase 2-4 实施计划

---

## 结论

采用**分层摘要架构**，以 **sumy (抽取式)** 作为默认基线，逐步引入 **tree-sitter (结构化)** 和 **transformers (生成式)** 作为增强。通过防腐层实现运行时降级，确保在任何情况下都有可靠的摘要输出。

**决策**: 接受此提案，Phase 2 优先实施 sumy 集成。
