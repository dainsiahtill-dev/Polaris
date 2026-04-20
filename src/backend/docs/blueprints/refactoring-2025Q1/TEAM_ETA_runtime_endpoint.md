# Team Eta: runtime_endpoint.py 重构蓝图

## 目标文件
`polaris/delivery/ws/runtime_endpoint.py` (1812行)

## 架构分析

### 当前问题
1. **端点混杂**: Session/Message/Export/ContextMemory 端点在同一文件
2. **请求/响应模型内嵌**: Pydantic模型与路由逻辑耦合
3. **辅助函数过多**: 大量 helper 函数散落

### 拆分方案

```
polaris/delivery/ws/
├── runtime_endpoint.py          # Facade (50行)
├── endpoints/
│   ├── __init__.py
│   ├── session.py               # Session端点 (400行)
│   ├── message.py               # Message端点 (400行)
│   ├── export.py                # Export端点 (300行)
│   ├── context_memory.py        # Context OS Memory端点 (300行)
│   └── models.py                # 请求/响应模型 (200行)
```

### 核心契约

```python
# endpoints/session.py
from fastapi import APIRouter

router = APIRouter(prefix="/session", tags=["session"])

@router.post("/")
async def create_session(request: CreateSessionRequest) -> SessionResponse:
    """创建会话。"""
    ...

# endpoints/message.py
router = APIRouter(prefix="/message", tags=["message"])

@router.post("/send")
async def send_message(request: SendMessageRequest) -> MessageResponse:
    """发送消息。"""
    ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31