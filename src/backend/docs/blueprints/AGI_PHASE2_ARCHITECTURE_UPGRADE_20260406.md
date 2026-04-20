# AGI骨架进化 Phase 2: 架构升级

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 8周
**人力**: 6人
**目标评分**: 75/100 (从68/100提升)

---

## 一、任务总览

| 任务 | 优先级 | 工作量 | 前置条件 |
|------|--------|--------|----------|
| gVisor容器沙箱集成 | P1 | 80h | Phase 1完成 |
| IPC机制(共享内存) | P1 | 64h | Phase 1完成 |
| 检查点恢复协议 | P2 | 48h | - |
| 知识图谱原型(Neo4j) | P2 | 64h | Phase 1完成 |
| 分布式任务队列 | P2 | 72h | - |

---

## 二、任务详情

### 2.1 任务T2-1: gVisor容器沙箱集成

**问题**: 当前仅逻辑沙箱，无OS级进程隔离。

**目标架构**:
```
┌─────────────────────────────────────────────────────────────┐
│                    容器沙箱分层                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Layer 4: 应用层                                            │
│  └── Agent Code + Tool Execution                           │
│                                                              │
│  Layer 3: gVisor User Namespace                            │
│  └── 文件系统: tmpfs + 受限root                            │
│  └── 网络: loopback only                                   │
│  └── PID: 独立命名空间                                      │
│                                                              │
│  Layer 2: Seccomp Filter                                   │
│  └── 系统调用白名单                                         │
│  └── 禁止: mount, syslog, reboot                           │
│                                                              │
│  Layer 1: Linux Cgroups                                    │
│  └── CPU: cpu.shares + cpu.cfs_quota_us                   │
│  └── Memory: memory.limit_in_bytes                        │
│  └── PIDs: pids.max                                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**实现方案**:

```python
# polaris/kernelone/sandbox/gvisor_runtime.py

from dataclasses import dataclass
from typing import Any, Protocol
import subprocess
import json
import tempfile
import os

class SandboxRuntime(Protocol):
    """沙箱运行时协议"""

    async def start(self) -> str:
        """启动沙箱，返回进程ID"""
        ...

    async def execute(self, command: str, cwd: str | None = None) -> CommandResult:
        """在沙箱中执行命令"""
        ...

    async def stop(self) -> None:
        """停止沙箱"""
        ...

@dataclass(frozen=True)
class SandboxConfig:
    """沙箱配置"""
    max_memory_bytes: int = 512 * 1024 * 1024  # 512MB
    max_cpu_seconds: float = 30.0
    max_pids: int = 64
    allowed_syscalls: tuple[str, ...] = (
        # 文件操作
        "read", "write", "openat", "close", "seek",
        # 内存映射
        "mmap", "mprotect", "munmap",
        # 进程
        "clone", "wait4", "exit", "getpid", "getppid",
        # 时间
        "clock_gettime", "nanosleep",
        # 网络(仅loopback)
        "socket", "bind", "listen", "accept",
        # 信号
        "rt_sigaction", "rt_sigreturn",
    )
    allowed_paths: tuple[str, ...] = ("/tmp", "/workspace")
    network_enabled: bool = False
    filesystem_readonly: bool = False

class GVisorRuntime:
    """gVisor沙箱运行时"""

    GVISOR_RUNSC_PATH: str = "/usr/bin/runsc"
    SANDBOX_DIR: str = "/var/run/polaris/sandboxes"

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._processes: dict[str, subprocess.Popen] = {}
        self._sandbox_dir = tempfile.mkdtemp(prefix="polaris_sandbox_")
        os.makedirs(self._sandbox_dir, exist_ok=True)

    async def start(self) -> str:
        """启动gVisor沙箱"""
        sandbox_id = f"sandbox_{os.getpid()}_{len(self._processes)}"

        # 创建沙箱配置
        config_path = os.path.join(self._sandbox_dir, f"{sandbox_id}.json")
        with open(config_path, "w") as f:
            json.dump({
                "max_memory_bytes": self._config.max_memory_bytes,
                "max_cpu_seconds": self._config.max_cpu_seconds,
                "max_pids": self._config.max_pids,
                "allowed_syscalls": list(self._config.allowed_syscalls),
                "allowed_paths": list(self._config.allowed_paths),
                "network_enabled": self._config.network_enabled,
                "readonly_filesystem": self._config.filesystem_readonly,
            }, f)

        # 验证runsc存在
        if not os.path.exists(self.GVISOR_RUNSC_PATH):
            raise SandboxNotAvailableError(
                f"gVisor runsc not found at {self.GVISOR_RUNSC_PATH}"
            )

        return sandbox_id

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
    ) -> CommandResult:
        """在沙箱中执行命令"""
        # 构建runsc命令
        runsc_cmd = [
            self.GVISOR_RUNSC_PATH,
            "run",
            "--network=host",  # 或 --network=none
            "--platform=ptrace",
            f"--cwd={cwd or '/workspace'}",
            f"sandbox-{sandbox_id}",
            "sh", "-c", command,
        ]

        try:
            result = await asyncio.create_subprocess_exec(
                *runsc_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._sandbox_dir,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    result.communicate(),
                    timeout=self._config.max_cpu_seconds,
                )
            except asyncio.TimeoutError:
                result.kill()
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr="Command timed out",
                    timed_out=True,
                )

            return CommandResult(
                exit_code=result.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=False,
            )

        except FileNotFoundError:
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command not found: {command.split()[0]}",
                timed_out=False,
            )
        except Exception as e:
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                timed_out=False,
            )

    async def stop(self, sandbox_id: str) -> None:
        """停止沙箱"""
        if sandbox_id in self._processes:
            proc = self._processes[sandbox_id]
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
            del self._processes[sandbox_id]

    async def cleanup(self) -> None:
        """清理所有沙箱资源"""
        for sandbox_id in list(self._processes.keys()):
            await self.stop(sandbox_id)

        # 清理沙箱目录
        import shutil
        if os.path.exists(self._sandbox_dir):
            shutil.rmtree(self._sandbox_dir, ignore_errors=True)

@dataclass(frozen=True)
class CommandResult:
    """命令执行结果"""
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

class SandboxNotAvailableError(Exception):
    """沙箱不可用错误"""
    pass

# 轻量级替代方案: seccomp + namespace

class SeccompSandbox:
    """基于seccomp的轻量级沙箱(无gVisor时fallback)"""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._allowed_syscalls = self._build_seccomp_allowlist()

    def _build_seccomp_allowlist(self) -> list[int]:
        """构建seccomp允许列表"""
        import ctypes
        import ctypes.util
        import os

        libseccomp = ctypes.CDLL(ctypes.util.find_library("seccomp"))

        allowlist: list[int] = []
        for syscall_name in self._config.allowed_syscalls:
            try:
                sc_num = libseccomp.seccomp_syscall_resolve_name(syscall_name.encode())
                if sc_num >= 0:
                    allowlist.append(sc_num)
            except Exception:
                pass

        return allowlist

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
    ) -> CommandResult:
        """使用seccomp限制执行命令"""
        # 构建BPF过滤程序
        bpf_prog = self._generate_bpf_program()

        # 使用unshare创建独立命名空间
        cmd = [
            "unshare",
            "--pid", "--fork", "--mount-proc",
            "--root", "/tmp",  # 限制root
            "--map-root-user",
            "--cgroup", f"/sys/fs/cgroup/polaris_{os.getpid()}",
            "sh", "-c", command,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.max_cpu_seconds,
            )

            return CommandResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=False,
            )

        except asyncio.TimeoutError:
            proc.kill()
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr="Command timed out",
                timed_out=True,
            )
```

**执行步骤**:
1. 创建`polaris/kernelone/sandbox/gvisor_runtime.py`
2. 实现`SandboxConfig`和`GVisorRuntime`
3. 实现`SeccompSandbox`作为fallback
4. 创建沙箱工厂和策略
5. 集成到`CommandExecutionService`
6. 添加安全测试

**验收标准**:
- [ ] gVisor沙箱启动成功
- [ ] 命令在沙箱中执行
- [ ] 资源限制生效
- [ ] Fallback到seccomp

---

### 2.2 任务T2-2: IPC机制(共享内存)

**问题**: Agent间通信必须依赖外部中间件，无本地IPC。

**目标架构**:
```
┌─────────────────────────────────────────────────────────────┐
│                    IPC架构                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SharedMemoryBus                         │   │
│  │  ├── publish(topic, message) → bool                 │   │
│  │  ├── subscribe(topic, handler) → Subscription       │   │
│  │  └── broadcast(agent_id, message) → bool            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              ActorMailbox                            │   │
│  │  ├── send(actor_id, message) → Future[Reply]       │   │
│  │  ├── receive() → Message                           │   │
│  │  └── become(handler)                               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SharedState                            │   │
│  │  ├── get(key) → value                               │   │
│  │  ├── set(key, value)                               │   │
│  │  ├── update(key, updater_fn) → new_value           │   │
│  │  └── watch(key, callback) → WatchHandle            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**实现方案**:

```python
# polaris/kernelone/ipc/shared_memory_bus.py

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar
import asyncio
import mmap
import json
import struct
import time
from collections import defaultdict
from concurrent.futures import Future

T = TypeVar("T")

@dataclass
class SharedMemoryConfig:
    """共享内存配置"""
    shm_size: int = 10 * 1024 * 1024  # 10MB
    max_messages: int = 1000
    message_ttl_seconds: float = 300.0
    cleanup_interval_seconds: float = 60.0

class SharedMemoryBus:
    """基于共享内存的IPC总线"""

    HEADER_FORMAT = "!QQQ"  # seq, timestamp, payload_len
    HEADER_SIZE = 24

    def __init__(self, config: SharedMemoryConfig) -> None:
        self._config = config
        self._shm: mmap.mmap | None = None
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._seq: int = 0
        self._local_cache: dict[str, Any] = {}

    async def initialize(self, namespace: str) -> None:
        """初始化共享内存"""
        shm_path = f"/dev/shm/polaris_{namespace}"
        try:
            fd = os.open(shm_path, os.O_RDWR | os.O_CREAT, 0o666)
            self._shm = mmap.mmap(fd, self._config.shm_size)
            os.close(fd)
        except PermissionError:
            # Fallback to anonymous shared memory
            self._shm = mmap.mmap(-1, self._config.shm_size)

    async def publish(self, topic: str, message: Any) -> bool:
        """发布消息到主题"""
        async with self._lock:
            envelope = MessageEnvelope(
                topic=topic,
                payload=message,
                timestamp=time.time(),
                sequence=self._seq,
            )

            # 序列化消息
            payload_bytes = json.dumps(message, default=str).encode("utf-8")

            # 写入共享内存
            if self._shm:
                offset = self._calculate_offset(self._seq)
                self._shm.seek(offset)
                self._shm.write(struct.pack(
                    self.HEADER_FORMAT,
                    self._seq,
                    envelope.timestamp,
                    len(payload_bytes),
                ))
                self._shm.write(payload_bytes)

            self._seq += 1

            # 本地通知订阅者
            for callback in self._subscribers.get(topic, []):
                asyncio.create_task(self._safe_invoke(callback, message))
            for callback in self._subscribers.get("*", []):
                asyncio.create_task(self._safe_invoke(callback, message))

            return True

    async def _safe_invoke(self, callback: Callable[[Any], None], message: Any) -> None:
        """安全调用回调"""
        try:
            callback(message)
        except Exception:
            pass  # 日志记录

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> Subscription:
        """订阅主题"""
        self._subscribers[topic].append(handler)
        return Subscription(topic=topic, handler=handler, bus=self)

    def unsubscribe(self, subscription: Subscription) -> None:
        """取消订阅"""
        if subscription.topic in self._subscribers:
            self._subscribers[subscription.topic].remove(subscription.handler)

    def _calculate_offset(self, seq: int) -> int:
        """计算序列号对应的偏移量"""
        # 简单环形缓冲区
        entry_size = self.HEADER_SIZE + (self._config.shm_size // self._config.max_messages)
        return (seq % self._config.max_messages) * entry_size

class MessageEnvelope:
    """消息信封"""
    def __init__(
        self,
        topic: str,
        payload: Any,
        timestamp: float,
        sequence: int,
    ) -> None:
        self.topic = topic
        self.payload = payload
        self.timestamp = timestamp
        self.sequence = sequence

@dataclass
class Subscription:
    """订阅句柄"""
    topic: str
    handler: Callable[[Any], None]
    bus: SharedMemoryBus

class ActorMailbox:
    """Actor邮箱"""

    def __init__(self, actor_id: str, bus: SharedMemoryBus) -> None:
        self._actor_id = actor_id
        self._bus = bus
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._handlers: dict[type, Callable[[Any], Any]] = {}
        self._running = False
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        """启动邮箱"""
        self._running = True
        self._subscription = self._bus.subscribe(
            f"actor.{self._actor_id}",
            lambda msg: self._queue.put_nowait(msg),
        )
        self._task = asyncio.create_task(self._process_messages())

    async def stop(self) -> None:
        """停止邮箱"""
        self._running = False
        if self._subscription:
            self._bus.unsubscribe(self._subscription)
        if hasattr(self, "_task"):
            self._task.cancel()

    async def send(self, target_id: str, message: Any) -> Any:
        """发送消息到目标Actor并等待回复"""
        future: Future[Any] = Future()

        async def handler(response: Any) -> None:
            if not future.done():
                future.set_result(response)

        reply_subscription = self._bus.subscribe(
            f"actor.{self._actor_id}.reply",
            handler,
        )

        await self._bus.publish(f"actor.{target_id}", Message(
            sender=self._actor_id,
            payload=message,
            reply_to=f"actor.{self._actor_id}.reply",
        ))

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            result = None
        finally:
            self._bus.unsubscribe(reply_subscription)

        return result

    async def receive(self) -> Message:
        """接收消息(阻塞)"""
        return await self._queue.get()

    def become(self, handler: Callable[[Any], Any]) -> None:
        """改变消息处理行为"""
        self._handlers[type(handler).__name__] = handler

    async def _process_messages(self) -> None:
        """处理消息循环"""
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                handler = self._handlers.get(type(message.payload).__name__)
                if handler:
                    result = handler(message.payload)
                    if asyncio.iscoroutine(result):
                        await result
            except asyncio.TimeoutError:
                continue
            except Exception:
                pass

@dataclass
class Message:
    """Actor消息"""
    sender: str
    payload: Any
    reply_to: str | None = None

class SharedState:
    """共享状态"""

    def __init__(self, bus: SharedMemoryBus) -> None:
        self._bus = bus
        self._local: dict[str, Any] = {}
        self._watches: dict[str, list[Callable[[str, Any], None]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        """获取值"""
        return self._local.get(key)

    async def set(self, key: str, value: Any) -> None:
        """设置值"""
        async with self._lock:
            old_value = self._local.get(key)
            self._local[key] = value

            # 广播变更
            for callback in self._watches.get(key, []):
                asyncio.create_task(callback(key, value))
            for callback in self._watches.get("*", []):
                asyncio.create_task(callback(key, value))

            # 发布到总线
            await self._bus.publish(f"state.{key}", {
                "key": key,
                "value": value,
                "old_value": old_value,
            })

    async def update(self, key: str, updater: Callable[[Any], Any]) -> Any:
        """原子更新"""
        async with self._lock:
            old_value = self._local.get(key)
            new_value = updater(old_value)
            self._local[key] = new_value

            for callback in self._watches.get(key, []):
                asyncio.create_task(callback(key, new_value))

            return new_value

    def watch(self, key: str, callback: Callable[[str, Any], None]) -> WatchHandle:
        """监视键变更"""
        self._watches[key].append(callback)
        return WatchHandle(key=key, callback=callback, state=self)

@dataclass
class WatchHandle:
    """监视句柄"""
    key: str
    callback: Callable[[str, Any], None]
    state: SharedState

    def cancel(self) -> None:
        """取消监视"""
        if self.key in self.state._watches:
            self.state._watches[self.key].remove(self.callback)
```

**执行步骤**:
1. 创建`polaris/kernelone/ipc/shared_memory_bus.py`
2. 实现`SharedMemoryBus`、`ActorMailbox`、`SharedState`
3. 创建IPC工厂
4. 集成到`NeuralSyndicateOrchestrator`
5. 编写集成测试

**验收标准**:
- [ ] 100+ Agent并发通信正常
- [ ] 消息延迟 < 10ms
- [ ] 无消息丢失
- [ ] 集成测试覆盖

---

### 2.3 任务T2-3: 检查点恢复协议

**问题**: 失败只能从头开始，无检查点机制。

**实现方案**:

```python
# polaris/kernelone/checkpoint/protocol.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator
import json
import hashlib

@dataclass(frozen=True)
class Checkpoint:
    """检查点"""
    checkpoint_id: str
    agent_id: str
    turn_number: int
    timestamp: datetime
    snapshot: AgentSnapshot
    metadata: dict[str, Any]
    checksum: str

@dataclass(frozen=True)
class AgentSnapshot:
    """Agent快照"""
    context_state: dict[str, Any]
    memory_state: dict[str, Any]
    tool_state: dict[str, Any]
    event_state: dict[str, Any]
    pending_messages: tuple[Message, ...]

@dataclass(frozen=True)
class CheckpointManifest:
    """检查点清单"""
    agent_id: str
    checkpoints: tuple[Checkpoint, ...]
    latest_checkpoint_id: str | None
    version: int

class CheckpointStore:
    """检查点存储"""

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path
        self._manifests: dict[str, CheckpointManifest] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        """保存检查点"""
        manifest = self._manifests.get(checkpoint.agent_id)

        if manifest:
            # 更新现有清单
            checkpoints = manifest.checkpoints + (checkpoint,)
            manifest = CheckpointManifest(
                agent_id=checkpoint.agent_id,
                checkpoints=checkpoints,
                latest_checkpoint_id=checkpoint.checkpoint_id,
                version=manifest.version + 1,
            )
        else:
            # 创建新清单
            manifest = CheckpointManifest(
                agent_id=checkpoint.agent_id,
                checkpoints=(checkpoint,),
                latest_checkpoint_id=checkpoint.checkpoint_id,
                version=1,
            )

        self._manifests[checkpoint.agent_id] = manifest

        # 持久化检查点
        checkpoint_path = self._get_checkpoint_path(checkpoint.agent_id, checkpoint.checkpoint_id)
        with open(checkpoint_path, "w") as f:
            json.dump(self._checkpoint_to_dict(checkpoint), f, default=str)

        # 持久化清单
        await self._save_manifest(checkpoint.agent_id)

    async def load(self, agent_id: str, checkpoint_id: str | None = None) -> Checkpoint | None:
        """加载检查点"""
        manifest = await self._load_manifest(agent_id)
        if not manifest:
            return None

        if checkpoint_id is None:
            checkpoint_id = manifest.latest_checkpoint_id
        if not checkpoint_id:
            return None

        checkpoint_path = self._get_checkpoint_path(agent_id, checkpoint_id)
        try:
            with open(checkpoint_path, "r") as f:
                data = json.load(f)
            return self._dict_to_checkpoint(data)
        except FileNotFoundError:
            return None

    async def list_checkpoints(self, agent_id: str) -> tuple[str, ...]:
        """列出检查点ID"""
        manifest = await self._load_manifest(agent_id)
        if not manifest:
            return ()
        return tuple(c.checkpoint_id for c in manifest.checkpoints)

    async def delete_old_checkpoints(self, agent_id: str, keep_last: int = 5) -> int:
        """删除旧检查点"""
        manifest = await self._load_manifest(agent_id)
        if not manifest or len(manifest.checkpoints) <= keep_last:
            return 0

        to_delete = manifest.checkpoints[:-keep_last]
        deleted = 0

        for checkpoint in to_delete:
            checkpoint_path = self._get_checkpoint_path(agent_id, checkpoint.checkpoint_id)
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
                deleted += 1

        # 更新清单
        new_manifest = CheckpointManifest(
            agent_id=agent_id,
            checkpoints=manifest.checkpoints[-keep_last:],
            latest_checkpoint_id=manifest.latest_checkpoint_id,
            version=manifest.version + 1,
        )
        self._manifests[agent_id] = new_manifest
        await self._save_manifest(agent_id)

        return deleted

    def _get_checkpoint_path(self, agent_id: str, checkpoint_id: str) -> str:
        """获取检查点文件路径"""
        return os.path.join(self._base_path, agent_id, f"{checkpoint_id}.json")

    async def _save_manifest(self, agent_id: str) -> None:
        """保存清单"""
        manifest = self._manifests[agent_id]
        manifest_path = os.path.join(self._base_path, agent_id, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(self._manifest_to_dict(manifest), f, default=str)

    async def _load_manifest(self, agent_id: str) -> CheckpointManifest | None:
        """加载清单"""
        manifest_path = os.path.join(self._base_path, agent_id, "manifest.json")
        try:
            with open(manifest_path, "r") as f:
                data = json.load(f)
            return self._dict_to_manifest(data)
        except FileNotFoundError:
            return None

    @staticmethod
    def _checkpoint_to_dict(cp: Checkpoint) -> dict[str, Any]:
        """检查点转字典"""
        return {
            "checkpoint_id": cp.checkpoint_id,
            "agent_id": cp.agent_id,
            "turn_number": cp.turn_number,
            "timestamp": cp.timestamp.isoformat(),
            "snapshot": {
                "context_state": cp.snapshot.context_state,
                "memory_state": cp.snapshot.memory_state,
                "tool_state": cp.snapshot.tool_state,
                "event_state": cp.snapshot.event_state,
                "pending_messages": [
                    {"sender": m.sender, "payload": m.payload}
                    for m in cp.snapshot.pending_messages
                ],
            },
            "metadata": cp.metadata,
            "checksum": cp.checksum,
        }

    @staticmethod
    def _dict_to_checkpoint(data: dict[str, Any]) -> Checkpoint:
        """字典转检查点"""
        return Checkpoint(
            checkpoint_id=data["checkpoint_id"],
            agent_id=data["agent_id"],
            turn_number=data["turn_number"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            snapshot=AgentSnapshot(
                context_state=data["snapshot"]["context_state"],
                memory_state=data["snapshot"]["memory_state"],
                tool_state=data["snapshot"]["tool_state"],
                event_state=data["snapshot"]["event_state"],
                pending_messages=tuple(
                    Message(**m) for m in data["snapshot"]["pending_messages"]
                ),
            ),
            metadata=data["metadata"],
            checksum=data["checksum"],
        )

    @staticmethod
    def compute_checksum(snapshot: AgentSnapshot) -> str:
        """计算快照校验和"""
        content = json.dumps({
            "context": snapshot.context_state,
            "memory": snapshot.memory_state,
            "tool": snapshot.tool_state,
            "event": snapshot.event_state,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

class CheckpointManager:
    """检查点管理器"""

    def __init__(
        self,
        store: CheckpointStore,
        interval_turns: int = 10,
        interval_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._interval_turns = interval_turns
        self._interval_seconds = interval_seconds
        self._last_checkpoint_time: dict[str, datetime] = {}

    async def should_checkpoint(self, agent_id: str, turn_number: int) -> bool:
        """判断是否应创建检查点"""
        last_time = self._last_checkpoint_time.get(agent_id)
        if last_time is None:
            return True

        elapsed = (datetime.now() - last_time).total_seconds()
        if elapsed >= self._interval_seconds:
            return True

        if turn_number % self._interval_turns == 0:
            return True

        return False

    async def create_checkpoint(
        self,
        agent_id: str,
        turn_number: int,
        snapshot: AgentSnapshot,
        metadata: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """创建检查点"""
        checkpoint_id = self._generate_checkpoint_id(agent_id, turn_number)
        checksum = CheckpointStore.compute_checksum(snapshot)

        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            turn_number=turn_number,
            timestamp=datetime.now(),
            snapshot=snapshot,
            metadata=metadata or {},
            checksum=checksum,
        )

        await self._store.save(checkpoint)
        self._last_checkpoint_time[agent_id] = datetime.now()

        return checkpoint

    async def restore(
        self,
        agent_id: str,
        checkpoint_id: str | None = None,
    ) -> Checkpoint | None:
        """恢复检查点"""
        checkpoint = await self._store.load(agent_id, checkpoint_id)
        if checkpoint:
            # 验证校验和
            expected = CheckpointStore.compute_checksum(checkpoint.snapshot)
            if expected != checkpoint.checksum:
                raise CheckpointCorruptedError(agent_id, checkpoint.checkpoint_id)
        return checkpoint

    def _generate_checkpoint_id(self, agent_id: str, turn_number: int) -> str:
        """生成检查点ID"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ckpt_{agent_id}_{turn_number}_{timestamp}"
```

**执行步骤**:
1. 创建`polaris/kernelone/checkpoint/protocol.py`
2. 实现`CheckpointStore`和`CheckpointManager`
3. 实现校验和验证
4. 集成到`TurnEngine`
5. 添加恢复测试

**验收标准**:
- [ ] 检查点创建成功
- [ ] 恢复后状态一致
- [ ] RTO < 5分钟
- [ ] 集成测试覆盖

---

### 2.4 任务T2-4: 知识图谱原型(Neo4j)

**问题**: 缺乏实体关系建模，仅有余弦相似度。

**实现方案**:

```python
# polaris/kernelone/knowledge_graph/neo4j_adapter.py

from dataclasses import dataclass
from typing import Any, Iterator
from neo4j import GraphDatabase

@dataclass(frozen=True)
class Entity:
    """实体"""
    id: str
    type: str  # "concept", "document", "code", "task"
    properties: dict[str, Any]
    embeddings: list[float] | None = None

@dataclass(frozen=True)
class Relationship:
    """关系"""
    source_id: str
    target_id: str
    type: str  # "depends_on", "implements", "calls", "relates_to"
    properties: dict[str, Any] = field(default_factory=dict)

class Neo4jKnowledgeGraph:
    """Neo4j知识图谱"""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
    ) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self) -> None:
        """关闭连接"""
        self._driver.close()

    async def add_entity(self, entity: Entity) -> None:
        """添加实体"""
        with self._driver.session() as session:
            session.run("""
                MERGE (e:Entity {id: $id})
                SET e.type = $type,
                    e.properties = $properties
                WITH e
                CALL db.create.setNodeVectorProperty(e, 'embedding', $embedding)
                YIELD node
                RETURN node
            """, id=entity.id, type=entity.type,
                properties=json.dumps(entity.properties),
                embedding=entity.embeddings or [])

    async def add_relationship(self, rel: Relationship) -> None:
        """添加关系"""
        with self._driver.session() as session:
            session.run("""
                MATCH (s:Entity {id: $source_id})
                MATCH (t:Entity {id: $target_id})
                MERGE (s)-[r:RELATES {type: $type}]->(t)
                SET r.properties = $properties
            """, source_id=rel.source_id, target_id=rel.target_id,
                type=rel.type, properties=json.dumps(rel.properties))

    async def query_by_embedding(
        self,
        embedding: list[float],
        top_k: int = 10,
        entity_type: str | None = None,
    ) -> list[tuple[Entity, float]]:
        """向量相似度查询"""
        with self._driver.session() as session:
            cypher = """
                WITH $embedding as target_embedding
                MATCH (e:Entity)
                WHERE e.embedding IS NOT NULL
                """
            if entity_type:
                cypher += " AND e.type = $entity_type "

            cypher += """
                WITH e, gds.similarity.cosine(e.embedding, target_embedding) as score
                WHERE score > 0.5
                RETURN e.id, e.type, e.properties, score
                ORDER BY score DESC
                LIMIT $top_k
            """

            result = session.run(cypher,
                embedding=embedding, entity_type=entity_type, top_k=top_k)

            return [
                (Entity(
                    id=record["e.id"],
                    type=record["e.type"],
                    properties=json.loads(record["e.properties"]),
                ), record["score"])
                for record in result
            ]

    async def query_hop(
        self,
        entity_id: str,
        hops: int = 2,
    ) -> list[Entity]:
        """Hop查询"""
        with self._driver.session() as session:
            result = session.run("""
                MATCH path = (start:Entity {id: $id})-[*1..%d]-(connected)
                RETURN DISTINCT connected
            """ % hops, id=entity_id)

            return [
                Entity(
                    id=record["connected"]["id"],
                    type=record["connected"]["type"],
                    properties=json.loads(record["connected"]["properties"]),
                )
                for record in result
            ]

    async def query_path(
        self,
        source_id: str,
        target_id: str,
    ) -> list[list[Entity]]:
        """最短路径查询"""
        with self._driver.session() as session:
            result = session.run("""
                MATCH path = shortestPath((s:Entity {id: $source_id})-[*]-(t:Entity {id: $target_id}))
                RETURN path
            """, source_id=source_id, target_id=target_id)

            paths = []
            for record in result:
                path_entities = []
                for node in record["path"].nodes:
                    path_entities.append(Entity(
                        id=node["id"],
                        type=node["type"],
                        properties=json.loads(node["properties"]),
                    ))
                paths.append(path_entities)

            return paths
```

---

### 2.5 任务T2-5: 分布式任务队列

**实现方案**:

```python
# polaris/kernelone/distributed/celery_backend.py

from celery import Celery
from dataclasses import dataclass

app = Celery("polaris")
app.config_from_object({
    "broker_url": "redis://localhost:6379/0",
    "result_backend": "redis://localhost:6379/1",
    "task_serializer": "json",
    "result_serializer": "json",
})

@dataclass
class DistributedTask:
    task_id: str
    agent_id: str
    payload: dict[str, Any]
    priority: int = 0

@app.task
def execute_agent_task(task: DistributedTask) -> dict[str, Any]:
    """Agent任务执行"""
    # 实现...
    pass

class DistributedTaskQueue:
    """分布式任务队列"""

    def __init__(self) -> None:
        self._app = app

    async def submit(self, task: DistributedTask) -> str:
        """提交任务"""
        result = execute_agent_task.apply_async(
            args=[task],
            priority=task.priority,
        )
        return result.id

    async def get_result(self, task_id: str, timeout: float = 30.0) -> Any:
        """获取结果"""
        result = self._app.AsyncResult(task_id)
        return result.get(timeout=timeout)
```

---

## 三、验收清单

```markdown
## Phase 2 验收检查单

### gVisor沙箱
- [ ] gVisor沙箱启动
- [ ] 资源限制生效
- [ ] Fallback工作

### IPC机制
- [ ] 100+ Agent并发
- [ ] 延迟 < 10ms
- [ ] 无消息丢失

### 检查点恢复
- [ ] 检查点创建
- [ ] 恢复一致
- [ ] RTO < 5分钟

### 知识图谱
- [ ] Neo4j连接
- [ ] 实体添加
- [ ] 关系查询

### 分布式队列
- [ ] Celery集成
- [ ] 任务分发
- [ ] 结果回收

### 整体
- [ ] AGI评分达到75/100
```
