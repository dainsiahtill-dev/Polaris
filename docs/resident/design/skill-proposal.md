# 技能提案确认流设计

> 关联: [实施路线图](../implementation-roadmap.md) Phase 1.3
> 依赖: Phase 1.1 统一变更证据模型
> 状态: DESIGN → IMPLEMENTATION
> 最后更新: 2024-03-08

---

## 核心概念

**SkillProposal** 是自动提取技能与人工确认之间的中间层。

```
Before (当前):
tick() 发现模式 ──→ 直接写入 skills ──→ 生效
                        ↑ 问题：无人工确认，可能误提取

After (Phase 1.3):
tick() 发现模式 ──→ 创建 SkillProposal ──→ 等待人工确认 ──→ 批准/拒绝
                           ↑                           ↓
                        pending_review            approved → 写入 skills
                                                  rejected → 丢弃
```

---

## 用户场景

### 场景 1: AGI 发现可复用模式
```
系统: 💡 AGI 发现可复用模式
      "异步错误处理模式" (置信度 0.87)
      基于 5 次相似决策提取

用户: [查看提案] [批准入库] [忽略]
```

### 场景 2: 查看技能提案详情
```
用户: 点击 [查看提案]
系统: 展示:
      - 模式名称: 异步错误处理模式
      - 置信度: 0.87
      - 来源决策:
        - 2024-03-01: 重构错误处理 (decision-001)
        - 2024-03-03: 添加 trace_id (decision-003)
        - ... (共5次)
      - 代码示例:
        ```typescript
        try { ... } catch (e) {
          await logError({ trace_id, error: e });
        }
        ```
```

### 场景 3: 批准入库
```
用户: 点击 [批准入库]
系统: ✅ 技能已入库
      "异步错误处理模式" 现在可用于后续决策推荐

ResidentService:
  - 创建 SkillArtifact
  - 关联到 extracted_from 的决策
  - SkillProposal 状态变为 merged
```

---

## 数据模型

### SkillProposal (新增)

```python
@dataclass
class SkillProposal:
    """技能提案 - 等待人工确认的技能提取"""

    proposal_id: str  # UUID
    created_at: str   # ISO timestamp
    updated_at: str   # ISO timestamp

    # 提案内容
    name: str                    # 技能名称
    description: str             # 技能描述
    pattern: str                 # 模式代码/模板
    context_type: str            # 适用上下文类型

    # 来源追踪
    extracted_from: List[str]    # 关联的 decision_ids
    evidence_bundle_ids: List[str]  # 关联的证据包

    # 统计置信度
    confidence: float            # 0.0 - 1.0
    occurrence_count: int        # 出现次数

    # 状态
    status: Literal["pending_review", "approved", "rejected", "merged"]

    # 审查记录
    reviewed_at: Optional[str]
    reviewed_by: Optional[str]   # reviewer identifier
    review_note: Optional[str]

    # 关联的技能（批准后）
    skill_id: Optional[str]      # 关联的 SkillArtifact ID
```

### SkillArtifact 扩展

```python
@dataclass
class SkillArtifact:
    # 已有字段...

    # Phase 1.3 新增
    extracted_from: List[str]        # 来源 decision_ids
    evidence_bundle_ids: List[str]   # 来源证据包
    proposal_id: Optional[str]       # 来源提案 ID
```

---

## 状态流转

```
┌─────────────────────────────────────────────────────────────┐
│                         SkillProposal                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────────┐    approve     ┌─────────────┐           │
│   │  pending    │ ─────────────→ │  approved   │ ───┐       │
│   │  _review    │                │             │    │       │
│   └─────────────┘                └─────────────┘    │       │
│          │                              │            │       │
│          │ reject                       │            │       │
│          ↓                              ↓            │       │
│   ┌─────────────┐                ┌─────────────┐    │       │
│   │  rejected   │                │   merged    │ ←──┘       │
│   │             │                │             │            │
│   └─────────────┘                └─────────────┘            │
│                                          ↑                  │
│                                          │ merge()         │
│                                          │ (自动)          │
└──────────────────────────────────────────┴──────────────────┘
```

---

## 核心算法

### 技能提取 → 提案生成

```python
def extract_skill_proposals(
    decisions: List[DecisionRecord],
    min_confidence: float = 0.7,
    min_occurrences: int = 3,
) -> List[SkillProposal]:
    """从决策记录中提取技能提案"""

    # 1. 按策略标签聚类
    clusters = defaultdict(list)
    for decision in decisions:
        for tag in decision.strategy_tags:
            clusters[tag].append(decision)

    proposals = []
    for tag, tag_decisions in clusters.items():
        # 2. 过滤低频次
        if len(tag_decisions) < min_occurrences:
            continue

        # 3. 计算置信度（基于成功率和证据质量）
        success_rate = sum(
            1 for d in tag_decisions if d.verdict.value == "success"
        ) / len(tag_decisions)

        has_evidence = sum(
            1 for d in tag_decisions if d.evidence_bundle_id
        ) / len(tag_decisions)

        confidence = (success_rate * 0.6) + (has_evidence * 0.4)

        if confidence < min_confidence:
            continue

        # 4. 生成提案
        proposal = SkillProposal(
            proposal_id=str(uuid.uuid4()),
            name=generate_skill_name(tag, tag_decisions),
            description=generate_description(tag_decisions),
            pattern=extract_common_pattern(tag_decisions),
            context_type=infer_context_type(tag_decisions),
            extracted_from=[d.decision_id for d in tag_decisions],
            evidence_bundle_ids=[
                d.evidence_bundle_id for d in tag_decisions
                if d.evidence_bundle_id
            ],
            confidence=round(confidence, 2),
            occurrence_count=len(tag_decisions),
            status="pending_review",
        )
        proposals.append(proposal)

    return sorted(proposals, key=lambda p: p.confidence, reverse=True)
```

---

## API 设计

### 后端 API

```python
# GET /api/v2/resident/skill-proposals
# 列出所有技能提案
{
  "items": [
    {
      "proposal_id": "proposal-xxx",
      "name": "异步错误处理模式",
      "description": "统一处理异步操作中的错误...",
      "confidence": 0.87,
      "occurrence_count": 5,
      "status": "pending_review",
      "created_at": "2024-03-08T10:00:00Z"
    }
  ],
  "pending_count": 3,
  "approved_count": 5,
  "rejected_count": 2
}

# GET /api/v2/resident/skill-proposals/{proposal_id}
# 获取提案详情
{
  "proposal_id": "proposal-xxx",
  "name": "异步错误处理模式",
  "description": "...",
  "pattern": "try { ... } catch (e) { await logError(...) }",
  "context_type": "async_function",
  "extracted_from": ["decision-001", "decision-003", ...],
  "evidence_bundle_ids": ["bundle-001", "bundle-003", ...],
  "confidence": 0.87,
  "occurrence_count": 5,
  "status": "pending_review"
}

# POST /api/v2/resident/skill-proposals/{proposal_id}/approve
# 批准提案
{
  "note": "这个模式很实用，批准入库"
}
# Response: { "skill": SkillArtifact, "proposal": SkillProposal }

# POST /api/v2/resident/skill-proposals/{proposal_id}/reject
# 拒绝提案
{
  "note": "过于具体，不具有通用性"
}
# Response: { "proposal": SkillProposal }
```

---

## 前端组件

### SkillProposalCard 组件

```typescript
interface SkillProposalCardProps {
  proposal: SkillProposalPayload;
  onApprove: (note?: string) => void;
  onReject: (note?: string) => void;
  onViewDetails: () => void;
}

// 卡片展示:
// ┌─────────────────────────────────────────┐
// │ 💡 异步错误处理模式                      │
// │ 置信度: ████████░░ 87% · 5次出现        │
// │                                         │
// │ [查看详情] [批准入库] [忽略]            │
// └─────────────────────────────────────────┘
```

### SkillProposalList 组件

```typescript
interface SkillProposalListProps {
  proposals: SkillProposalPayload[];
  filter: 'pending' | 'approved' | 'rejected' | 'all';
}

// 分组展示:
// 待处理 (3)
//   - ProposalCard 1
//   - ProposalCard 2
// 已批准 (5)
//   - ProposalCard 3 (已入库)
```

---

## 集成点

### tick() 集成

```python
# app/resident/service.py

def tick(self, *, force: bool = False) -> dict[str, Any]:
    # ... 现有逻辑 ...

    # Phase 1.3: 生成技能提案（而不是直接写入 skills）
    skill_proposals = self.skill_foundry.extract_proposals(decisions)

    for proposal in skill_proposals:
        # 检查是否已存在相同提案
        if not self.storage.find_skill_proposal_by_pattern(proposal.pattern):
            self.storage.save_skill_proposal(proposal)

    return self.get_status(include_details=True)
```

### ResidentWorkspace 集成

```typescript
// AGI 工作区新增 "技能提案" 标签页

const TAB_OPTIONS = ['overview', 'goals', 'decisions', 'skill-proposals'];

// 显示未处理提案数徽章
<Badge>{pendingProposals.length}</Badge>
```

---

## 实现步骤

1. **数据模型** (`models.py`)
   - 新增 `SkillProposal` dataclass
   - 扩展 `SkillArtifact` 添加来源字段

2. **存储层** (`storage.py`)
   - `load_skill_proposals()` / `save_skill_proposals()`
   - `find_skill_proposal_by_pattern()`

3. **服务层** (`service.py`)
   - `list_skill_proposals()`
   - `get_skill_proposal(proposal_id)`
   - `approve_skill_proposal(proposal_id, note)`
   - `reject_skill_proposal(proposal_id, note)`
   - 修改 `tick()` 生成提案而非直接写入 skills

4. **API 层** (`api/v2/resident.py`)
   - `GET /skill-proposals`
   - `GET /skill-proposals/{id}`
   - `POST /skill-proposals/{id}/approve`
   - `POST /skill-proposals/{id}/reject`

5. **前端组件**
   - `SkillProposalCard.tsx`
   - `SkillProposalList.tsx`
   - 在 `ResidentWorkspace` 添加 "技能提案" 标签

---

## 验收标准

```python
# E2E 测试
it('should create skill proposal from tick', async () => {
  // 1. 执行多次相似决策
  for (let i = 0; i < 5; i++) {
    await residentService.recordDecision({
      actor: 'director',
      stage: 'coding',
      summary: f'实现异步错误处理 {i}',
      strategy_tags: ['async_error_handling'],
      verdict: 'success',
    });
  }

  // 2. 执行 tick
  await residentService.tick();

  // 3. 检查生成提案
  const proposals = await residentService.listSkillProposals();
  expect(proposals.length).toBeGreaterThan(0);
  expect(proposals[0].status).toBe('pending_review');

  // 4. 批准提案
  const approved = await residentService.approveSkillProposal(
    proposals[0].proposal_id,
    '批准入库'
  );
  expect(approved.status).toBe('merged');
  expect(approved.skill_id).toBeDefined();

  // 5. 技能已创建
  const skills = await residentService.listSkills();
  expect(skills.map(s => s.skill_id)).toContain(approved.skill_id);
});
```

---

## 相关文档

- [统一变更证据模型](./evidence-bundle.md) (Phase 1.1)
- [目标执行投影](./goal-execution-projection.md) (Phase 1.2)
- [实施路线图](../implementation-roadmap.md)
