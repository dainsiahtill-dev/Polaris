# SEARCH/REPLACE Block 编辑格式迁移蓝图

**版本**: v1.0  
**日期**: 2026-04-12  
**状态**: RFC / 待实施  
**负责人**: 架构委员会  

---

## 1. 执行摘要 (Executive Summary)

### 1.1 问题陈述

当前 Polaris 的代码编辑工具 `precision_edit` 采用 JSON 参数格式 (`search`/`replace`)，导致：
- **字符级幻觉**: `return0` 代替 `return 0`
- **缩进丢失**: LLM 在 JSON 转义上下文中丢失代码缩进
- **JSON 转义税**: 模型注意力被拆分到代码逻辑和转义规则
- **编辑成功率低**: 基准测试显示 30%+ 的编辑失败源于格式问题

### 1.2 解决方案

**废弃 JSON 传参，全面采用 Aider 风格的纯文本 SEARCH/REPLACE 块格式：**

```markdown
<<<< SEARCH
    if not values:
        return 0
====
    if not values:
        raise ValueError("Cannot compute median of empty list")
>>>> REPLACE
```

### 1.3 预期收益

| 指标 | 当前 (JSON) | 目标 (SEARCH/REPLACE) | 提升 |
|-----|------------|---------------------|------|
| 编辑成功率 | ~65% | ~95% | +30% |
| 缩进错误率 | ~25% | ~2% | -23% |
| Token 效率 | 中等 | 高 | +15% |
| 模型认知负荷 | 高 (需处理转义) | 低 (原生代码) | 显著降低 |

### 1.4 行业对标

- **Aider**: 采用 SEARCH/REPLACE 块，代码编辑成功率行业领先
- **Claude Code**: 内部使用类似 diff 格式
- **GitHub Copilot Workspace**: 采用代码块语义化编辑

---

## 2. 架构设计 (Architecture Design)

### 2.1 新旧架构对比

```
┌─────────────────────────────────────────────────────────────────────┐
│                         当前架构 (JSON-based)                        │
├─────────────────────────────────────────────────────────────────────┤
│  LLM Response                                                       │
│  {                                                                  │
│    "tool": "precision_edit",                                        │
│    "args": {                                                        │
│      "file": "src/median.py",                                       │
│      "search": "    if not values:\n        return 0",  ← 易出错    │
│      "replace": "    if not values:\n        raise ..."             │
│    }                                                                │
│  }                                                                  │
│         │                                                           │
│         ▼                                                           │
│  JSON Parser ──► Tool Executor ──► File System                      │
│         ↑                                                           │
│    问题: 转义字符、缩进丢失、格式崩塌                                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      新架构 (SEARCH/REPLACE Block)                   │
├─────────────────────────────────────────────────────────────────────┤
│  LLM Response (自然语言流)                                          │
│  ```                                                                │
│  我来修复空列表处理问题：                                            │
│                                                                     │
│  <<<< SEARCH:src/median.py                                          │
│      if not values:                                                 │
│          return 0                                                   │
│  ====                                                               │
│      if not values:                                                 │
│          raise ValueError("Cannot compute median of empty list")    │
│  >>>> REPLACE                                                       │
│  ```                                                                │
│         │                                                           │
│         ▼                                                           │
│  EditBlock Parser ──► Search/Replace Engine ──► File System         │
│         ↑                                                           │
│    优势: 零转义、原生代码、视觉锚定                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

```
┌──────────────────────────────────────────────────────────────────────┐
│                    SEARCH/REPLACE 编辑架构                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐         │
│  │ LLM Response │────▶│ EditBlock    │────▶│ Search/      │         │
│  │ (Raw Text)   │     │ Parser       │     │ Replace      │         │
│  └──────────────┘     └──────────────┘     │ Engine       │         │
│                                            └──────┬───────┘         │
│                                                   │                  │
│         ┌────────────────────────────────────────┘                  │
│         ▼                                                           │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐         │
│  │ Fuzzy Match  │────▶│ Indent       │────▶│ File Write   │         │
│  │ (容错匹配)    │     │ Preservation │     │ (原子操作)    │         │
│  └──────────────┘     └──────────────┘     └──────────────┘         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.3 编辑块格式规范

**标准格式:**
```markdown
<<<< SEARCH[:filepath]
<原始代码行 1>
<原始代码行 2>
...
====
<替换代码行 1>
<替换代码行 2>
...
>>>> REPLACE
```

**变体支持:**
```markdown
<<<<<<< SEARCH          (Git 风格)
=======
>>>>>>> REPLACE

<<<< SEARCH             (简化风格)
====
>>>> REPLACE
```

**多文件支持:**
```markdown
<<<< SEARCH:src/median.py
...
====
...
>>>> REPLACE

<<<< SEARCH:tests/test_median.py
...
====
...
>>>> REPLACE
```

---

## 3. 详细实施计划 (Implementation Plan)

### 3.1 Phase 1: 基础设施准备 (Week 1)

**包 1: EditBlock 解析器强化** (负责人: 研发A)
- **文件**: `polaris/kernelone/editing/editblock_engine.py`
- **任务**:
  - [ ] 强化多文件编辑块解析
  - [ ] 添加 filepath 推断逻辑（从上下文或显式声明）
  - [ ] 支持模糊文件名匹配
  - [ ] 添加 fence 清理（```）
- **验收标准**:
  - 解析成功率 > 99%
  - 支持 10+ 种常见格式变体

**包 2: 搜索替换引擎集成** (负责人: 研发B)
- **文件**: `polaris/kernelone/editing/search_replace_engine.py`
- **任务**:
  - [ ] 集成 Aider 的 10 种匹配策略
  - [ ] 实现缩进保留转换
  - [ ] 添加模糊匹配回退
  - [ ] 支持 "..." 省略号锚点
- **验收标准**:
  - 精确匹配率 > 90%
  - 模糊匹配成功率 > 95%

**包 3: 工具定义更新** (负责人: 研发C)
- **文件**: `polaris/kernelone/tool_execution/contracts.py`
- **任务**:
  - [ ] 废弃 `precision_edit` (标记为 deprecated)
  - [ ] 新增 `edit_blocks` 工具
  - [ ] 更新 `edit_file` 支持 block 格式
  - [ ] 更新工具别名映射
- **验收标准**:
  - 新工具 Schema 通过验证
  - 向后兼容性测试通过

### 3.2 Phase 2: 提示词系统改造 (Week 2)

**包 4: System Prompt 重构** (负责人: 研发D)
- **文件**: 
  - `polaris/cells/roles/kernel/internal/prompt_templates.py`
  - `polaris/cells/roles/profile/internal/builtin_profiles.py`
- **任务**:
  - [ ] 编写新的编辑工具使用说明
  - [ ] 添加 SEARCH/REPLACE 格式示例
  - [ ] 移除 precision_edit 相关示例
  - [ ] 添加错误处理指南
- **验收标准**:
  - 新提示词通过人工审查
  - LLM 输出格式符合率 > 95%

**包 5: Few-shot 示例库** (负责人: 研发E)
- **文件**: 
  - `polaris/cells/roles/kernel/internal/prompt_templates.py`
  - `polaris/cells/roles/assets/few_shot/`
- **任务**:
  - [ ] 创建 20+ 个编辑场景示例
  - [ ] 覆盖 Python/JS/TS/YAML 等语言
  - [ ] 包含多行编辑、缩进处理等复杂案例
  - [ ] 添加常见错误及修正示例
- **验收标准**:
  - 示例覆盖率 > 90% 编辑场景

### 3.3 Phase 3: 执行引擎改造 (Week 3-4)

**包 6: Tool Executor 重构** (负责人: 研发F)
- **文件**: 
  - `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py`
  - `polaris/kernelone/llm/toolkit/executor/handlers/repo.py`
- **任务**:
  - [ ] 实现 `edit_blocks` 处理器
  - [ ] 集成 `editblock_engine`
  - [ ] 添加块解析错误处理
  - [ ] 实现原子写入（失败回滚）
- **验收标准**:
  - 编辑成功率 > 95%
  - 零数据丢失风险

**包 7: 质量检查器更新** (负责人: 研发G)
- **文件**: `polaris/cells/roles/kernel/internal/quality_checker.py`
- **任务**:
  - [ ] 更新编辑质量检查逻辑
  - [ ] 添加 SEARCH/REPLACE 块格式校验
  - [ ] 检测常见错误模式
  - [ ] 集成到重试循环
- **验收标准**:
  - 错误检测率 > 90%

**包 8: 输出解析器更新** (负责人: 研发H)
- **文件**: `polaris/cells/roles/kernel/internal/output_parser.py`
- **任务**:
  - [ ] 添加块提取逻辑
  - [ ] 支持从自然语言中提取编辑块
  - [ ] 处理多个连续编辑块
  - [ ] 与现有 tool_call 解析集成
- **验收标准**:
  - 解析准确率 > 95%

### 3.4 Phase 4: 测试与验证 (Week 5-6)

**包 9: 测试套件** (负责人: 研发I)
- **任务**:
  - [ ] 单元测试：编辑块解析器 (50+ 案例)
  - [ ] 单元测试：搜索替换引擎 (50+ 案例)
  - [ ] 集成测试：端到端编辑流程 (30+ 案例)
  - [ ] 回归测试：与现有功能兼容性
  - [ ] 性能测试：大规模文件编辑
- **验收标准**:
  - 测试覆盖率 > 85%
  - 所有测试通过

**包 10: 基准测试与调优** (负责人: 研发J)
- **任务**:
  - [ ] 构建编辑成功率基准测试集
  - [ ] 对比新旧架构成功率
  - [ ] 调优模糊匹配阈值
  - [ ] 文档化最佳实践
  - [ ] 编写运维手册
- **验收标准**:
  - 成功率提升 > 25%
  - 文档完整度 100%

---

## 4. 10人团队分工

### 团队结构

| 角色 | 负责人 | 职责 | 交付物 |
|-----|-------|------|-------|
| **技术负责人** | 架构师 | 整体架构设计、代码审查、风险把控 | 架构文档、审查报告 |
| **引擎组 (3人)** | 研发A/B/F | 解析器、搜索替换、执行器 | 核心引擎代码 |
| **提示词组 (2人)** | 研发D/E | System Prompt、Few-shot 示例 | 提示词模板库 |
| **工具链组 (2人)** | 研发C/H/G | 工具定义、解析器、质量检查 | 工具链代码 |
| **测试组 (2人)** | 研发I/J | 测试用例、基准测试、文档 | 测试套件、报告 |

### 详细分工

```
Week 1: 基础设施
├── 研发A (EditBlock Parser)
│   └── polaris/kernelone/editing/editblock_engine.py
├── 研发B (Search/Replace Engine)
│   └── polaris/kernelone/editing/search_replace_engine.py
└── 研发C (Tool Contracts)
    └── polaris/kernelone/tool_execution/contracts.py

Week 2: 提示词系统
├── 研发D (System Prompt)
│   └── polaris/cells/roles/kernel/internal/prompt_templates.py
└── 研发E (Few-shot Examples)
    └── polaris/cells/roles/assets/few_shot/

Week 3-4: 执行引擎
├── 研发F (Tool Executor)
│   └── polaris/kernelone/llm/toolkit/executor/handlers/
├── 研发G (Quality Checker)
│   └── polaris/cells/roles/kernel/internal/quality_checker.py
└── 研发H (Output Parser)
    └── polaris/cells/roles/kernel/internal/output_parser.py

Week 5-6: 测试验证
├── 研发I (Test Suite)
│   └── polaris/kernelone/editing/tests/
│   └── polaris/cells/roles/kernel/tests/
└── 研发J (Benchmark & Docs)
    └── docs/blueprints/SEARCH_REPLACE_MIGRATION_REPORT.md
```

---

## 5. 时间表与里程碑

### Gantt 图

```
Week:    1       2       3       4       5       6       7       8
         ├───────┼───────┼───────┼───────┼───────┼───────┼───────┤
Phase 1  [███████]                                               
  包1     [████]                                                 
  包2      [████]                                                
  包3        [██]                                                
                                                                  
Phase 2          [███████]                                       
  包4            [████]                                          
  包5              [███]                                         
                                                                  
Phase 3                  [████████████]                          
  包6                    [████████]                              
  包7                        [████]                              
  包8                          [████]                            
                                                                  
Phase 4                                  [████████████]          
  包9                                  [████████]                
  包10                                     [██████]              
                                                                  
里程碑1 ▲              ▲              ▲              ▲
       引擎就绪      提示词就绪      功能完成       发布就绪
```

### 关键里程碑

| 里程碑 | 日期 | 交付物 | 验收标准 |
|-------|------|-------|---------|
| M1: 引擎就绪 | Week 1 结束 | 解析器 + 搜索替换引擎 | 单元测试 100% 通过 |
| M2: 提示词就绪 | Week 2 结束 | System Prompt + 示例库 | LLM 格式符合率 > 95% |
| M3: 功能完成 | Week 4 结束 | 完整工具链 | 集成测试 100% 通过 |
| M4: 发布就绪 | Week 6 结束 | 测试报告 + 文档 | 成功率提升 > 25% |

---

## 6. 风险与缓解策略

### 6.1 技术风险

| 风险 | 可能性 | 影响 | 缓解策略 |
|-----|-------|------|---------|
| 解析器边界情况处理不完善 | 中 | 高 | 建立 100+ 边界测试案例库 |
| 模糊匹配误伤正确代码 | 低 | 高 | 相似度阈值保守设置 (0.85+) |
| 向后兼容性问题 | 中 | 中 | 保留旧工具 2 个版本，逐步废弃 |
| 性能下降 | 低 | 中 | 预编译正则、缓存解析结果 |

### 6.2 项目风险

| 风险 | 可能性 | 影响 | 缓解策略 |
|-----|-------|------|---------|
| 开发进度延迟 | 中 | 中 | 每周同步，预留 1 周缓冲 |
| 测试覆盖率不足 | 低 | 高 | 强制 PR 审查覆盖率门槛 |
| 团队协作问题 | 低 | 中 | 每日站会、代码审查制度 |

---

## 7. 成功指标 (KPIs)

### 7.1 技术指标

| KPI | 基线 | 目标 | 测量方法 |
|-----|------|------|---------|
| 编辑成功率 | 65% | 95% | 基准测试套件 |
| 缩进错误率 | 25% | 2% | 日志分析 |
| 平均重试次数 | 2.5 | 1.2 | 工具调用统计 |
| 解析失败率 | 5% | 0.5% | 错误日志监控 |

### 7.2 业务指标

| KPI | 基线 | 目标 | 测量方法 |
|-----|------|------|---------|
| 任务完成时间 | 100% | -30% | 基准测试计时 |
| LLM Token 消耗 | 100% | -15% | API 调用日志 |
| 用户满意度 | 3.5/5 | 4.5/5 | 内部反馈调查 |

---

## 8. 附录

### 8.1 参考文档

- [Aider Editing Parity Audit](../audit/aider_editing_parity_audit.md)
- [EditBlock Engine](../../polaris/kernelone/editing/editblock_engine.py)
- [Search/Replace Engine](../../polaris/kernelone/editing/search_replace_engine.py)
- [Aider Source](https://github.com/paul-gauthier/aider)

### 8.2 术语表

| 术语 | 定义 |
|-----|------|
| EditBlock | Aider 风格的 SEARCH/REPLACE 代码块 |
| Fence | Markdown 代码围栏 (```) |
| Cognitive Load | 模型处理任务时的认知负担 |
| JSON Escaping Tax | JSON 转义带来的额外开销 |
| Fuzzy Match | 容错匹配算法 |
| Context Padding | 在编辑块中包含上下文行 |

### 8.3 审批记录

| 版本 | 日期 | 审批人 | 备注 |
|-----|------|-------|------|
| 0.1 | 2026-04-12 | 架构师 | 初稿 |
| 1.0 | TBD | 技术委员会 | 待审批 |

---

*本蓝图遵循 Polaris 架构标准 v2.0*
