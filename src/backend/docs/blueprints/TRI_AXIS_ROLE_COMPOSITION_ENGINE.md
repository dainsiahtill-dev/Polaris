# 三轴正交角色引擎 (Tri-Axis Role Composition Engine)

**版本**: v1.0  
**状态**: 已批准实施  
**创建日期**: 2026-04-12  
**负责人**: Chief Engineer  

---

## 1. 背景与问题

### 1.1 现状问题

当前 Polaris 的角色系统存在以下问题：

| 问题 | 描述 | 严重度 |
|------|------|--------|
| 组合爆炸 | 3个Polaris角色 × N种专业领域 × M种人格 = O(n×m×k) 配置 | 高 |
| 职责耦合 | `director` 模板既定义"调度流程"又内嵌"编码标准" | 中 |
| 硬编码约束 | `<thinking>` 约束分散在各个模板中，难以统一管理 | 中 |
| 扩展性差 | 新增专业领域需要修改角色核心逻辑 | 高 |

### 1.2 解决方案

采用**"正交组合 (Orthogonal Composition)" + "Mixin 模式"**，将角色拆解为三个完全独立的维度：

```
最终执行体 = System_Anchor ⊕ Profession ⊕ Persona
```

---

## 2. 架构设计

### 2.1 三轴定义

| 轴 | 名称 | 职责 | 示例 |
|----|------|------|------|
| **X轴** | System Anchor | 决定"处于什么流程节点"和"宏观职责" | PM (需求拆解)、Director (调度规划)、QA (质量把控) |
| **Y轴** | Profession | 决定"具备什么硬核技能"和"微观执行标准" | Python 首席架构师、K8s 运维专家 |
| **Z轴** | Persona | 决定"用什么语气说话"和"性格特征" | Director (严谨/文言)、赛博朋克 (极客/冷酷) |

### 2.2 组合示例

```
Director + Director + Python架构师
= 一个带着"Director"性格的"Director"，
  正在以"Python 首席架构师"的标准审查代码
```

---

## 3. 目录结构

```
polaris/assets/roles/
├── anchors/                          # [X轴] Polaris 系统基座
│   ├── pm.yaml                       # 需求拆解与定义工作流
│   ├── director.yaml                 # 任务调度与蓝图规划约束
│   └── qa.yaml                       # 验收标准与审查流
│
├── professions/                      # [Y轴] 专业能力插件
│   ├── python_principal_architect.yaml
│   ├── security_auditor.yaml
│   ├── devops_engineer.yaml
│   └── _base.yaml                    # 专业基类模板
│
├── personas/                         # [Z轴] 人格特征层
│   ├── gongbu_shilang.yaml          # Director
│   ├── shangshuling.yaml            # PM
│   ├── zhongshuling.yaml            # Architect
│   └── mentu_xiaozhong.yaml         # QA
│
└── formats/                          # [公共] 强制输出模板
    ├── architecture_blueprint.yaml
    └── code_review_report.yaml
```

---

## 4. 配置 Schema

### 4.1 Anchor 配置 (anchors/director.yaml)

```yaml
id: polaris_director
type: system_anchor
version: "1.0"

name: Director
description: Polaris 执行引擎，负责任务调度与蓝图规划

capabilities:
  - workflow_orchestration
  - state_management
  - tool_delegation

macro_workflow:
  stages:
    - id: analysis
      name: 需求分析
      description: 理解上下文，拆解任务
    - id: blueprint
      name: 蓝图规划
      description: 调用 Profession 能力进行技术方案设计
    - id: execution
      name: 委派执行
      description: 将任务委派给下游 Agent
  transitions:
    - from: analysis
      to: blueprint
      condition: has_context
    - from: blueprint
      to: execution
      condition: blueprint_approved

output_constraint:
  thinking_tag: "<thinking>"
  thinking_max_tokens: 200
  required_order: ["thinking", "content"]
```

### 4.2 Profession 配置 (professions/python_principal_architect.yaml)

```yaml
id: python_principal_architect
type: profession
version: "1.0"

name: Python 首席架构师
description: 世界顶级的 Python 首席架构师，指挥 10 名精英工程师

identity: |
  你是一位世界顶级的 Python 首席架构师（Principal Architect），
  你正在指挥和调度一支由 10 名 10x 资深 Python 工程师组成的精英团队。
  你的目标是创建、重构并维护一个绝对可靠、稳定且具备极高工程素养的系统。

expertise:
  - 系统架构设计
  - 高并发分布式系统
  - Python 性能优化
  - 领域驱动设计 (DDD)

engineering_standards:
  coverage_mode: strict

  standards:
    code_quality:
      - 严格执行 PEP 8，基于 Ruff 和 Black
      - 清晰、表意明确的命名
    architecture_principles:
      - 单一职责原则（SRP）
      - 低耦合、高内聚
      - 模块可测试、可维护
    type_safety:
      level: strict
      requirements:
        - 100% 类型注解
        - 使用 | 联合类型、type 泛型别名
        - 零警告通过 mypy --strict
    documentation:
      - 关键类和复杂函数必须有 docstring
      - 使用 Google/NumPy docstring 风格

  red_lines:
    - 严禁过度设计（Over-engineering）
    - 严禁炫技
    - 严禁隐藏副作用
    - 严禁重复代码（DRY）
    - 严禁裸露的 except:
    - 严禁 any 类型

task_protocols:
  new_code:
    quality_bar: production_ready
    requires_documentation: true
    requires_tests: true
    test_coverage_min: 80

  refactor:
    backward_compatible: true
    migration_path: required
    regression_tests: mandatory

  code_review:
    levels:
      - blocker: 必须修改，否则无法合并
      - suggestion: 建议修改，有助于提升代码质量
      - nitpick: 可选优化，不影响合并
    output_format: structured

  bug_fix:
    root_cause_analysis: mandatory
    regression_test: required
    fix_verification: required

output_format:
  default: standard

  formats:
    standard:
      sections:
        - Result
        - Analysis
        - Risks & Boundaries
        - Testing
        - Self-Check
        - Future Optimization
```

### 4.3 Persona 配置 (personas/gongbu_shilang.yaml)

```yaml
id: gongbu_shilang
type: persona
version: "1.0"

name: Director
description: 大国工匠与总工程师，务实严谨

traits: |
  大国工匠与总工程师。务实、严谨、以结果为导向。
  擅长分解复杂任务为可执行步骤。
  在阐述复杂架构时，喜欢用工程建造的隐喻。

tone: |
  沉稳、专业、惜字如金。直接指出核心，不讲废话。
  在验收时严格把关，在委派时清晰明确。

vocabulary:
  - "臣已核实"
  - "当前工程进度"
  - "按律不可"
  - "验证无误"
  - "蓝图已定"
  - "委派执行"

expression:
  greeting: "臣听令。"
  thinking_prefix: "<thinking>"
  thinking_suffix: "</thinking>"
  conclusion_prefix: "综上"
  farewell: "钦此。"
```

### 4.4 Recipe 配置 (recipes/)

```yaml
# 内置角色配方
senior_python_architect:
  anchor: director
  profession: python_principal_architect
  persona: gongbu_shilang

security_architect:
  anchor: qa
  profession: security_auditor
  persona: mentu_xiaozhong
```

---

## 5. 核心引擎设计

### 5.1 RoleComposer 类

```python
# polaris/kernelone/role/composer.py

class RoleComposer:
    """
    角色组合引擎
    核心职责：将 Anchor + Profession + Persona 三层配置
              组合成完整的 System Prompt
    """

    def compose(
        self,
        anchor_id: str,
        profession_id: str,
        persona_id: str,
        task_type: str,
        context: dict | None = None
    ) -> ComposedPrompt:
        # 1. 加载三层配置
        anchor = self._load_anchor(anchor_id)
        profession = self._load_profession(profession_id)
        persona = self._load_persona(persona_id)

        # 2. 生成各层 Prompt
        identity_prompt = self._build_identity_prompt(anchor, profession, persona)
        workflow_prompt = self._build_workflow_prompt(anchor, profession, task_type)
        standards_prompt = self._build_standards_prompt(profession)
        protocols_prompt = self._build_protocols_prompt(profession, task_type)
        format_prompt = self._build_format_prompt(profession, task_type)

        # 3. 组装
        return ComposedPrompt(
            system_prompt=self._assemble(
                identity_prompt,
                workflow_prompt,
                standards_prompt,
                protocols_prompt,
                format_prompt
            ),
            metadata=PromptMetadata(
                anchor_id=anchor_id,
                profession_id=profession_id,
                persona_id=persona_id,
                task_type=task_type
            )
        )
```

### 5.2 缓存架构

| 层级 | Cache Key | TTL | 说明 |
|------|-----------|-----|------|
| L1 | `hash(anchor + profession + persona)` | 1h | 身份与标准，极少变更 |
| L2 | `hash(profession + task_type)` | 1h | 工作流与协议 |
| L3 | 全局安全边界 | 10min | 独立缓存 |
| L4/L5 | 不缓存 | - | 任务输入，实时推断 |

---

## 6. 实现计划

### Phase 1: 配置层解耦 (Week 1)
- [ ] 创建 `polaris/assets/roles/` 目录结构
- [ ] 迁移现有 `personas.yaml` 到 `personas/*.yaml`
- [ ] 创建 `anchors/pm.yaml`, `anchors/director.yaml`, `anchors/qa.yaml`
- [ ] 创建 `professions/_base.yaml` 专业基类
- [ ] 创建 `formats/*.yaml` 输出格式模板
- [ ] 编写 YAML Schema 验证

### Phase 2: PromptBuilder 重构 (Week 2-3)
- [ ] 新增 `polaris/kernelone/role/` 模块
- [ ] 实现 `RoleComposer` 类
- [ ] 实现 `AnchorLoader`, `ProfessionLoader`, `PersonaLoader`
- [ ] 实现 `ConfigMerger` 配置合并器
- [ ] 保持向后兼容：旧 `role` 参数自动映射为 `recipe`
- [ ] 单元测试覆盖

### Phase 3: TurnEngine Stage 化 (Week 4-5)
- [ ] 将硬编码 workflow 抽象为 Stage 声明
- [ ] 新增 `StageTransitionEngine`
- [ ] 支持多 Stage 间的状态传递
- [ ] 单元测试覆盖

### Phase 4: 热替换机制 (Week 6-7)
- [ ] ContextOS 支持 `prompt_modifiers`
- [ ] ToolLoopController 支持注入 `LoopPolicy`
- [ ] Provider 动态绑定支持
- [ ] 集成测试覆盖

### Phase 5: 专业角色落地 (Week 8-10)
- [ ] `python_principal_architect.yaml` 完整实现
- [ ] `security_auditor.yaml` 实现
- [ ] `devops_engineer.yaml` 实现
- [ ] 文档更新
- [ ] 端到端测试

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 向后兼容破坏 | 高 | Phase 2 保留旧接口，自动映射 |
| 缓存不一致 | 中 | 引入版本号机制 |
| 性能下降 | 低 | 分层缓存 + 懒加载 |
| 配置膨胀 | 中 | YAML Schema 验证 + linter |

---

## 8. 验收标准

1. **组合性验证**: 任意 Anchor + Profession + Persona 组合能生成有效 Prompt
2. **向后兼容**: 现有 `role="director"` 调用路径不变
3. **热切换验证**: 同一 Session 内可动态切换 Profession
4. **类型安全**: 零 mypy 警告
5. **测试覆盖**: 核心组件 >90% 覆盖率

---

## 9. 相关文档

- [架构标准](../../docs/AGENT_ARCHITECTURE_STANDARD.md)
- [KernelOne 架构规范](../../docs/KERNELONE_ARCHITECTURE_SPEC.md)
- [Tool Alias 设计指南](../../docs/governance/TOOL_ALIAS_DESIGN_GUIDE.md)
