# EvidenceBundle 统一证据模型设计

> 关联: [实施路线图](../implementation-roadmap.md) Phase 1.1
> 状态: DESIGN
> 最后更新: 2024-03-08

---

## 问题陈述

当前 Decision 记录缺乏与代码变更的强关联：
- 只知道"Director 做了优化"，不知道"具体改了哪些文件"
- 想回顾决策时，无法看到当时的代码 diff
- 审查和实验需要重复实现变更捕获逻辑

## 目标

建立统一的 `EvidenceBundle` 模型，支持：
1. **决策追溯** - 每个决策附带完整的变更证据
2. **代码审查** - 复用同一套变更表示
3. **实验对比** - 对比两个策略的变更差异
4. **工作树友好** - 支持未提交的修改（Polaris 常见场景）

---

## 数据模型

### EvidenceBundle

```python
@dataclass
class EvidenceBundle:
    """变更证据包 - Decision/Review/Experiment 共用"""

    # 标识
    bundle_id: str                      # UUID v4
    created_at: datetime               # 创建时间
    workspace: str                     # 关联工作区

    # Git 锚点（解决"未提交修改"问题）
    base_sha: str                      # 执行前的 commit (HEAD)
    head_sha: Optional[str]            # 执行后的 commit（可能为空）
    working_tree_dirty: bool           # 是否有未提交修改

    # 变更内容（主要证据）
    change_set: List[FileChange]       # 文件变更列表

    # 执行结果
    test_results: Optional[TestRunEvidence]
    performance_snapshot: Optional[PerfEvidence]
    static_analysis: Optional[StaticAnalysisEvidence]

    # 来源关联（用于追溯）
    source_type: Literal['director_run', 'manual', 'experiment', 'review']
    source_run_id: Optional[str]       # Director run_id
    source_task_id: Optional[str]      # 具体任务
    source_goal_id: Optional[str]      # 上层目标

    # 元数据
    metadata: Dict[str, Any]           # 扩展字段
```

### FileChange

```python
@dataclass
class FileChange:
    """单个文件变更"""

    path: str                          # 文件路径（相对 workspace）
    change_type: Literal['added', 'modified', 'deleted', 'renamed']

    # Git blob sha（用于去重和引用）
    before_sha: Optional[str]          # 变更前 blob sha
    after_sha: Optional[str]           # 变更后 blob sha

    # 内容（小文件直接存，大文件存引用）
    patch: Optional[str]               # unified diff（< 100KB）
    patch_ref: Optional[str]           # 大文件存到 .polaris/bundles/{bundle_id}/{path}.patch

    # 元数据
    language: Optional[str]            # 编程语言（来自文件扩展名）
    lines_added: int
    lines_deleted: int

    # 关联信息
    related_symbols: List[str]         # 变更涉及的符号（函数/类名）
```

### 测试结果证据

```python
@dataclass
class TestRunEvidence:
    """测试运行结果"""
    test_command: str                  # 运行的测试命令
    exit_code: int
    total_tests: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    failed_tests: List[str]            # 失败的测试名列表
    coverage_percent: Optional[float]  # 覆盖率
    raw_output_ref: str                # 原始输出文件引用
```

### 性能证据

```python
@dataclass
class PerfEvidence:
    """性能快照"""
    benchmark_command: Optional[str]
    metrics: Dict[str, float]          # {"latency_p99": 120.5, "throughput": 1500.0}
    baseline_comparison: Optional[Dict[str, float]]  # 与基线对比
    flamegraph_ref: Optional[str]      # 火焰图文件引用
```

---

## 与现有模型的关系

```
DecisionRecord
├── decision_id
├── evidence_bundle_id ───────> EvidenceBundle
│                                   ├── change_set[]
│                                   ├── test_results
│                                   └── ...
└── ...

GoalProposal
├── goal_id
└── materialized_bundle_id ───> EvidenceBundle (执行后的状态)

ExperimentResult
├── experiment_id
├── baseline_bundle_id ───────> EvidenceBundle
└── counterfactual_bundle_id ─> EvidenceBundle
```

---

## 存储策略

### 小文件内联（< 100KB）
```json
{
  "bundle_id": "uuid",
  "change_set": [
    {
      "path": "src/utils/error.ts",
      "patch": "@@ -1,5 +1,8 @@\n+import { trace } from './trace';\n...",
      // patch 直接存 JSON
    }
  ]
}
```

### 大文件外置（>= 100KB）
```json
{
  "bundle_id": "uuid",
  "change_set": [
    {
      "path": "large-file.bin",
      "patch": null,
      "patch_ref": ".polaris/bundles/uuid/large-file.bin.patch"
    }
  ]
}
```

### 存储位置
```
workspace/
└── .polaris/
    ├── bundles/                    # 证据包外置文件
    │   └── {bundle_id}/
    │       ├── large-file.patch
    │       └── test-output.log
    └── runtime/
        └── evidence_index.jsonl    # 证据包索引（便于遍历）
```

---

## API 设计

### EvidenceBundleService

```python
class EvidenceBundleService:
    """证据包服务"""

    def create_from_working_tree(
        self,
        workspace: str,
        base_sha: str,
        source_type: str,
        source_run_id: Optional[str] = None,
        source_task_id: Optional[str] = None,
        test_results: Optional[TestRunEvidence] = None,
    ) -> EvidenceBundle:
        """从当前工作树创建证据包"""

    def create_from_director_run(
        self,
        workspace: str,
        run_id: str,
        task_results: List[TaskResult],
    ) -> EvidenceBundle:
        """从 Director run 结果创建证据包"""

    def get_bundle(self, bundle_id: str) -> Optional[EvidenceBundle]:
        """获取证据包详情"""

    def compare_bundles(
        self,
        base_bundle_id: str,
        head_bundle_id: str,
    ) -> BundleComparison:
        """对比两个证据包"""

    def find_related_decisions(
        self,
        bundle_id: str,
    ) -> List[DecisionRecord]:
        """查找关联的决策记录"""
```

---

## 与 FileEventBroadcaster 的集成

复用现有的文件变更捕获：

```python
# file_event_broadcaster.py 已有逻辑
async def on_file_change(event: FileChangeEvent):
    # 现有：广播到 WebSocket
    await broadcast(event)

    # 新增：如果有活跃的 evidence collection，记录到 bundle
    if active_collection := get_active_collection(event.workspace):
        active_collection.record_change(event)
```

---

## 前端展示

### EvidenceViewer 组件

```typescript
interface EvidenceViewerProps {
  bundleId: string;
}

// 展示内容：
// 1. Git 信息：base_sha → head_sha（或 working tree）
// 2. 文件变更列表（复用 RealTimeFileDiff）
// 3. 测试结果摘要
// 4. 性能对比（如果有）
// 5. 关联决策链接
```

---

## 实现步骤

1. **数据模型** (`evidence_models.py`)
   - 定义 dataclass
   - 序列化/反序列化方法

2. **服务层** (`evidence_service.py`)
   - EvidenceBundleService 实现
   - 与 git 的集成（获取 diff、sha）

3. **存储层**
   - 内联/外置判断逻辑
   - 文件读写

4. **集成到 Resident** (`service.py`)
   - `record_decision()` 自动创建 bundle

5. **数据库迁移**
   - `decisions` 表新增 `evidence_bundle_id`

6. **前端组件**
   - EvidenceViewer
   - 集成到 DecisionCard

---

## 验收标准

```python
def test_evidence_bundle_workflow():
    # 1. 创建证据包
    bundle = service.create_from_working_tree(
        workspace="/path/to/project",
        base_sha="abc123",
        source_type="director_run",
        source_run_id="run-456",
    )
    assert bundle.bundle_id is not None
    assert bundle.base_sha == "abc123"
    assert len(bundle.change_set) >= 0

    # 2. 关联到决策
    decision = resident_service.record_decision({
        'actor': 'director',
        'summary': '优化错误处理',
        'verdict': 'success',
        'evidence_bundle_id': bundle.bundle_id,  # 关键字段
    })

    # 3. 获取证据详情
    loaded = service.get_bundle(decision.evidence_bundle_id)
    assert loaded.change_set[0].patch is not None

    # 4. 支持工作树（未提交）
    assert bundle.working_tree_dirty in [True, False]
```

---

## 相关文档

- [实施路线图](../implementation-roadmap.md)
- [Resident 工程 RFC](../resident-engineering-rfc.md)
- [Resident API](../resident-api.md)
