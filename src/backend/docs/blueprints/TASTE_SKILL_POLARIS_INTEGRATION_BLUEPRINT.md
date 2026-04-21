# Taste-Skill → Polaris 原生工程架构集成蓝图

**版本**: v1.0  
**日期**: 2026-04-21  
**作者**: Principal Architect (AI Engineering Team)  
**范围**: `polaris/kernelone/cognitive/` — Design Quality Enforcement Layer

---

## 1. 背景与动机

### 1.1 来源
[taste-skill](https://github.com/Leonxlnx/taste-skill) 是一个 AI 前端设计质量 enforcement skill 集合，包含 8 个变体（taste / gpt-taste / redesign / soft / minimalist / brutalist / stitch / output）。其核心贡献是**将主观设计决策转化为可验证的工程参数系统**。

### 1.2 问题域
当前 AI 生成前端代码存在三类系统性质量问题：
1. **设计平庸化（Slop）**：Inter/Roboto 字体泛滥、#000000 纯黑、AI 废话文案
2. **输出截断（Laziness）**：骨架输出、占位符注释、省略实现
3. **意图漂移（Variance Leak）**：用户要求"实验性"，输出却是居中对称布局

### 1.3 目标
将 taste-skill 的设计原则转化为 Polaris **可运行、可测试、可进化**的 Python 工程模块，作为 Cognitive Runtime 的输出质量门禁（Output Quality Gate）。

**约束**：绝不干扰非设计代码生成（Python/SQL/后端逻辑），必须实现严格的域隔离。

---

## 2. 系统架构

### 2.1 高层架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Cognitive Runtime (Orchestrator)                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              Output Quality Gate ( taste-skill layer )       │    │
│  │                                                              │    │
│  │   ┌──────────────┐    ┌─────────────────────────────────┐   │    │
│  │   │   Dispatcher  │───▶│   Domain Isolation Router       │   │    │
│  │   │  (P0-Pre)     │    │   (Extension + Content Heuristic)│   │    │
│  │   └──────────────┘    └─────────────────────────────────┘   │    │
│  │           │                              │                   │    │
│  │           ▼                              ▼                   │    │
│  │   ┌──────────────┐              ┌─────────────────┐         │    │
│  │   │  BYPASS      │              │  VALIDATE       │         │    │
│  │   │  (Python/SQL │              │  (UI/DESIGN)    │         │    │
│  │   │   /Markdown) │              │                 │         │    │
│  │   └──────────────┘              └────────┬────────┘         │    │
│  │                                          │                   │    │
│  │           ┌──────────────────────────────┼──────────┐       │    │
│  │           ▼                              ▼          ▼       │    │
│  │   ┌──────────────┐    ┌──────────────┐  ┌──────────────┐   │    │
│  │   │ Anti-Slop    │    │ Completeness │  │  Self-Correction│   │    │
│  │   │ (P0-B)       │    │ (P0-C)       │  │  (Severity-Gated)│   │    │
│  │   │              │    │              │  │                 │   │    │
│  │   │ • Font       │    │ • Placeholder│  │  ERROR → LLM    │   │    │
│  │   │ • Color      │    │ • Skeleton   │  │          Rewrite│   │    │
│  │   │ • Content    │    │ • Min-lines  │  │                 │   │    │
│  │   │ • Layout     │    │              │  │  WARNING → Silent│   │    │
│  │   │ • Motion     │    │              │  │          Rewrite │   │    │
│  │   └──────────────┘    └──────────────┘  │  + PhantomState  │   │    │
│  │           │                              │       Hydration  │   │    │
│  │           ▼                              └──────────┬───────┘   │    │
│  │   ┌──────────────┐                                  │           │    │
│  │   │ Violations   │◀─────────────────────────────────┘           │    │
│  │   │ (ERROR/WARN) │                                              │    │
│  │   └──────────────┘                                              │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Design Token System (P1)                      │
│                                                                      │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│   │ Typography   │  │ Color Palette│  │ Motion Preset│              │
│   │ (density-aware│  │ (HSL-based)  │  │ (spring-phys)│              │
│   └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │              Design System Spec + Exporter (P0-D)            │   │
│   │         (7-section DESIGN.md semantic format)                │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责矩阵

| 模块 | 文件路径 | 职责 | 输入 | 输出 |
|------|---------|------|------|------|
| **Domain Isolation** | `validators/dispatcher.py` | 路由生成内容到对应验证链路 | 文件路径 + 内容 | GenerationDomain + bypass/check 决策 |
| **DesignQualityDials** | `design_quality.py` | 三轴参数系统（variance/motion/density） | 用户意图 / 配置 | LayoutMode / SpacingTier / MotionPresetKey |
| **FontValidator** | `validators/font_validator.py` | 检测 banned/allowed 字体 | CSS/JSX 内容 | ValidationViolation[] |
| **ColorValidator** | `validators/color_validator.py` | HSL 颜色质量分析 | CSS 内容 | ValidationViolation[] |
| **ContentValidator** | `validators/content_validator.py` | Emoji/假名/AI 废话检测 | 文本内容 | ValidationViolation[] |
| **LayoutValidator** | `validators/layout_validator.py` | 布局反模式检测 | CSS/JSX 内容 | ValidationViolation[] |
| **MotionValidator** | `validators/motion_validator.py` | 动画属性门禁 | CSS 内容 | ValidationViolation[] |
| **OutputAntiSlop** | `validators/output_antislop.py` | 组合 5 个子 validator | 内容 + dials | 聚合 Violations |
| **CompletenessEnforcer** | `validators/completeness_enforcer.py` | 输出完整性检查 | 生成代码 | ValidationViolation[] |
| **DesignSystemSpec** | `design_system.py` | 7-section 设计规范数据结构 | 设计参数 | spec 对象 + markdown |
| **TypographyTokens** | `design_tokens/typography.py` | 字体层级令牌 | density | Token 规范 |
| **ColorPalette** | `design_tokens/palette.py` | 颜色校准器 | 基色 + dials | 调色板 |
| **MotionPresets** | `design_tokens/motion.py` | 动画预设库 | motion 轴 | spring/framer/gsap 配置 |

---

## 3. 核心数据流

### 3.1 单次验证请求流

```
1. LLM 生成代码片段 (e.g. Button.tsx)
   │
2. CognitiveValidatorDispatcher.validate(
       file_path="Button.tsx",
       content=generated_code,
       config=ValidationConfig(design_dials=dials)
   )
   │
3. resolve_domain() → UI_COMPONENT (via .tsx extension)
   │
4. Domain gate: UI_COMPONENT ∈ {UI, DESIGN} → CHECK (not bypass)
   │
5. _run_antislop(content, domain, config)
   │   ├── FontValidator.validate() → [] (no banned fonts)
   │   ├── ColorValidator.validate() → [Violation(ERROR, "#000000 banned")]
   │   ├── ContentValidator.validate() → [] (no AI slop)
   │   ├── LayoutValidator.validate() → [Violation(WARN, "h-screen")]
   │   └── MotionValidator.validate() → [] (no linear easing)
   │
6. _run_completeness(content, domain, config)
   │   └── OutputCompletenessEnforcer.validate() → [] (not skeleton)
   │
7. Aggregate violations
   │
8. Severity-gated self-correction
   ├── ERROR: trigger LLM rewrite with enriched prompt
   └── WARNING: silent AST rewrite + PhantomStateHydrator sync
```

### 3.2 Design Dials 驱动流

```
User Prompt: "创建一个极简风格的登录页面"
   │
Layer 2 (Session Intent Parsing)
   ├── keyword: "极简" → minimalist preset
   └── dials = DesignQualityDials.minimalist()  # (3, 2, 2)
   │
Layer 3 (Execution Context)
   ├── dials.layout_mode → SYMMETRIC_CENTERED
   ├── dials.spacing_tier → GALLERY
   └── dials.motion_preset_key → HOVER_ONLY
   │
Downstream Consumption
   ├── LayoutValidator: variance=3 → 允许 centered hero, 3-col grid OK
   ├── TypographyTokens: density=2 → text-4xl display, max-w-[65ch] body
   ├── ColorPalette: density=2 → 低对比度 palette
   └── MotionPresets: motion=2 → 仅 hover 状态动画
```

---

## 4. 技术选型理由

### 4.1 纯 Python Scanner（零外部 AST 依赖）

**决策**：不使用 Babel / SWC / Tree-sitter 解析前端代码。

**理由**：
1. **部署零依赖**：Polaris 是纯 Python 后端，引入 Node.js 解析器增加运维复杂度
2. **验证场景匹配**：taste-skill 验证的是设计模式（pattern matching），不是语义正确性
3. **性能足够**：regex + lightweight CSS property scanner 对 1-50KB 代码片段完全够用
4. **Warning 级修复**：静默改写只需文本替换，不需要精确 AST 定位

**权衡**：对于复杂的嵌套 CSS 规则，regex 可能误报。通过 `ValidationSeverity.WARNING` 降级处理，避免阻断合法代码。

### 4.2 frozen dataclass + `__post_init__` 验证

**决策**：所有配置/规格对象使用 `frozen=True` dataclass。

**理由**：
1. **不可变性保证**：设计参数在 turn 内不可变，防止 validator 链中途被篡改
2. **哈希兼容性**：frozen dataclass 可放入 set/dict，便于缓存和去重
3. **防御性边界**：`__post_init__` 在构造期抛出 ValueError，fail-fast

### 4.3 Severity-Gated Hybrid Self-Correction

**决策**：Error → LLM rewrite；Warning → Silent rewrite。

**理由**：
1. **Error 级**（如 #000000）：涉及设计语义判断，需要 LLM 理解上下文后重写
2. **Warning 级**（如 h-screen → min-h-dvh）：机械替换，AST/text 改写即可
3. **PhantomStateHydrator**：静默修复后必须写回 session context，否则 LLM 后续 turn 会回归旧模式

### 4.4 Domain Isolation（GenerationDomain）

**决策**：文件扩展名 + 内容启发式双重路由。

**理由**：
1. **安全红线**：Python/SQL 代码绝不能进入 CSS 验证器（防止误报）
2. **最小侵入**：扩展名路由 O(1)，无性能影响
3. **兜底机制**：内容启发式处理无扩展名的 inline 代码块

---

## 5. 接口契约

### 5.1 Validator 统一协议

```python
class ValidatorProtocol(Protocol):
    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]: ...
```

### 5.2 ValidationViolation 结构

```python
class ValidationViolation(NamedTuple):
    rule: str           # 规则标识符，如 "banned_color_black"
    severity: ValidationSeverity  # error | warning | info
    message: str        # 人类可读描述
    location: str | None  # 位置：CSS selector / file:line
    domain: GenerationDomain | None
    fix_hint: str | None  # 修复建议
```

### 5.3 Dispatcher 公共 API

```python
# 单次验证
violations = dispatcher.validate(
    file_path="src/components/Button.tsx",
    content=generated_code,
    config=ValidationConfig(design_dials=dials),
)

# 批量验证
results = dispatcher.validate_batch([
    ("Button.tsx", code1),
    ("styles.css", code2),
])

# 全局单例
dispatcher = get_validator_dispatcher()
```

---

## 6. 风险与边界

### 6.1 已知局限

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| Regex 误报 | Warning 级误触发静默改写 | 严格测试覆盖 + 可配置 severity 降级 |
| CSS-in-JS 复杂嵌套 | LayoutValidator 可能漏报 | 仅覆盖常见模式（styled-components / Tailwind），不追求 100% |
| 颜色空间转换误差 | HSL 解析近似值 | 使用整数舍入，容忍 ±1° hue 误差 |
| 多文件关联缺失 | 跨文件设计不一致无法检测 | P2 阶段引入 DesignSystemSpec 级别的一致性检查 |

### 6.2 不处理的场景

- **运行时行为验证**：不执行/渲染代码，仅静态扫描
- **无障碍（a11y）检查**：超出 taste-skill 范围，由独立 validator 处理
- **响应式设计完整性**：不验证断点覆盖，仅验证单断点质量
- **第三方库设计质量**：不验证 node_modules 中的代码

---

## 7. 验证策略

### 7.1 单元测试矩阵

| 测试文件 | 覆盖模块 | 关键用例 |
|---------|---------|---------|
| `test_validators_dispatcher.py` | Domain Isolation | 21 个扩展名路由 + 8 个内容启发式 + 域隔离保证 |
| `test_font_validator.py` | FontValidator | Banned 检测 / Allowed 通过 / 大小写不敏感 |
| `test_color_validator.py` | ColorValidator | #000000 / neon purple / saturation > 80% |
| `test_content_validator.py` | ContentValidator | Emoji / AI 废话 / 假名 / lorem ipsum |
| `test_layout_validator.py` | LayoutValidator | h-screen / calc% / 3-col grid / centered hero |
| `test_motion_validator.py` | MotionValidator | linear easing / layout anim / spin |
| `test_completeness_enforcer.py` | Completeness | placeholder / skeleton / min-lines |
| `test_output_antislop.py` | OutputAntiSlop | 组合器聚合 / fast-fail |
| `test_design_system.py` | DesignSystemSpec | spec 构造 / exporter markdown |
| `test_design_tokens.py` | Token System | typography density-aware / palette HSL / motion spring |

### 7.2 集成验证命令

```bash
# 静态检查
ruff check polaris/kernelone/cognitive/validators/ --fix
ruff format polaris/kernelone/cognitive/validators/
mypy polaris/kernelone/cognitive/validators/

# 测试
pytest polaris/kernelone/cognitive/tests/test_validators_*.py -v

# 全链路冒烟
cd src/backend && python -c "
from polaris.kernelone.cognitive import (
    DesignQualityDials, GenerationDomain,
    CognitiveValidatorDispatcher, ValidationConfig,
)
dials = DesignQualityDials.minimalist()
dispatcher = CognitiveValidatorDispatcher()
print('Domain:', dispatcher.resolve_domain('Button.tsx'))
print('Dials:', dials.to_dict())
"
```

---

## 8. 演进路线

### Phase 1 (当前): P0-B ~ P1 基础能力
- 5 个 Anti-Slop validator
- Completeness enforcer
- Design system spec + exporter
- 3 个 design token 子系统

### Phase 2 (未来): P2 专业变体
- BrutalistDesignTokens（Swiss Industrial / Tactical Telemetry）
- MinimalistPaletteResolver（精确 hex + 语义 slot）
- Dark mode 自动推导

### Phase 3 (未来): 运行时集成
- 与 `TurnTransactionController` 的 post-generation hook 对接
- Violation → LLM prompt enrichment 的自动链路
- PhantomStateHydrator 的 session context 同步

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-04-19 | 初始规划 — P0-A (DesignQualityDials) |
| v0.2 | 2026-04-21 | 完成 P0-Pre (Domain Isolation) + P0-A，新增蓝图文档 |
| v1.0 | 2026-04-21 | 全量执行规划 — P0-B ~ P1 完整架构定义 |
