# VCR Recording HTTP Details 扩展蓝图

**版本**: v1.0
**日期**: 2026-04-04
**状态**: 待执行
**优先级**: P1

---

## 1. 问题描述

### 1.1 现状

`polaris/kernelone/benchmark/reproducibility/vcr.py` 中的 `Recording` dataclass 缺少 HTTP 协议级细节：

```python
@dataclass
class Recording:
    request_key: str      # SHA256 hash of args, not HTTP-level
    response: dict        # Full response dict, but not structured
    timestamp: str
    metadata: dict | None
```

### 1.2 与 ShadowReplay 的关系

| 特性 | CacheReplay (vcr.py) | ShadowReplay (shadow_replay/) |
|------|---------------------|------------------------------|
| 粒度 | 函数级（@replay 装饰器） | HTTP 级（httpx 拦截） |
| 录制内容 | 函数参数 + response dict | method/URL/headers/body/status/latency |
| 匹配方式 | SHA256(args) | body_hash + URL + method |
| 用途 | 单元测试/可重复性 | 集成测试/HTTP 调试 |
| 已有 HTTP 细节 | ❌ | ✅ |

### 1.3 决策：收敛还是扩展？

**选择：扩展 vcr.py Recording，保持两系统独立**

理由：
1. `CacheReplay` 是轻量级方案，适合快速录制函数调用
2. `ShadowReplay` 是重量级方案，适合完整 HTTP 调试
3. 两者服务不同场景，保持独立更灵活
4. 扩展 `Recording` 使其包含 HTTP 细节，不会与 `ShadowReplay` 冲突

---

## 2. 扩展方案

### 2.1 Recording 扩展字段

```python
@dataclass
class Recording:
    """Immutable recording of a request-response pair."""

    # 原有字段（保持兼容）
    request_key: str
    response: dict[str, Any]
    timestamp: str
    metadata: dict[str, Any] | None = None

    # 新增 HTTP 细节字段
    method: str = ""                          # HTTP method (GET, POST, etc.)
    url: str = ""                             # Full URL
    request_headers: dict[str, str] | None = None
    request_body: str | None = None           # Request body as string
    response_status: int = 0                 # HTTP status code
    response_headers: dict[str, str] | None = None
    latency_ms: float = 0.0                  # Request latency
```

### 2.2 向后兼容性

- 所有新字段提供默认值（`""`、`None`、`0`），确保现有代码不受影响
- `request_key` 保持不变（基于 args hash），确保现有缓存文件兼容

### 2.3 数据流

```
CacheReplay.replay() 调用
    ↓
检查 cache 中是否有 Recording
    ↓
如果有 → 返回 Recording（含 HTTP 细节）
如果没有 → 执行真实调用 → 录制结果（含 HTTP 细节）
    ↓
保存到 cache 文件
```

---

## 3. 实现步骤

### 3.1 修改 Recording dataclass

文件：`polaris/kernelone/benchmark/reproducibility/vcr.py`

1. 扩展 `Recording` dataclass 添加新字段
2. 保持向后兼容（默认值）

### 3.2 修改 _load_recording / _save_recording

文件：`polaris/kernelone/benchmark/reproducibility/vcr.py`

1. 确保新字段被正确序列化/反序列化
2. 旧 cache 文件缺少新字段时应能正常加载

### 3.3 修改 _make_key（如需要）

- 当前 `request_key` 基于 `json.dumps(args)` 的 SHA256
- 如果需要基于 HTTP 细节匹配，添加 `request_body_hash` 字段

---

## 4. 技术细节

### 4.1 序列化兼容性

```python
# 新字段提供默认值，确保旧 cache 文件兼容
@dataclass
class Recording:
    request_key: str
    response: dict[str, Any]
    timestamp: str
    metadata: dict[str, Any] | None = None
    # 新增（默认空值，兼容旧格式）
    method: str = ""
    url: str = ""
    request_headers: dict[str, str] | None = None
    request_body: str | None = None
    response_status: int = 0
    response_headers: dict[str, str] | None = None
    latency_ms: float = 0.0
```

### 4.2 与 HTTPExchange 的差异

| 字段 | Recording (扩展后) | HTTPExchange (shadow_replay) |
|------|-------------------|------------------------------|
| request_body | `str \| None` | `bytes \| None` |
| response_body | 嵌入 response dict | 独立 `response_body` 字段 |

**差异原因**：`Recording.response` 是通用的 `dict`，而 `HTTPExchange` 有独立的 `response_body` bytes 字段。

---

## 5. 验证计划

```bash
# 1. ruff 检查
python -m ruff check polaris/kernelone/benchmark/reproducibility/vcr.py --fix

# 2. mypy 检查
python -m mypy polaris/kernelone/benchmark/reproducibility/vcr.py --strict

# 3. 现有测试确保通过
python -m pytest polaris/kernelone/benchmark/reproducibility/tests/ -v
```

---

## 6. 风险评估

| 风险 | 等级 | 缓解 |
|-----|------|------|
| 旧 cache 文件缺少新字段 | LOW | 默认值确保兼容 |
| 序列化大小增加 | LOW | HTTP 细节通常较小 |
| 现有测试可能依赖旧结构 | LOW | 所有新字段有默认值 |

---

**文档状态**: 待执行
**预计工时**: 1h
