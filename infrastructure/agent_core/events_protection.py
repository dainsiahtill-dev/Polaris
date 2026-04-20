#!/usr/bin/env python3
"""
events.jsonl 物理防护系统
实现 AGENTS.md v2.2 中的状态机不变量保护
"""

import os
import json
import hashlib
import time
import sys
from datetime import datetime
from pathlib import Path


def _resolve_runtime_artifact(rel_suffix: str) -> Path:
    override_root = os.environ.get("HP_AGENT_CORE_RUNTIME_ROOT", "").strip()
    if override_root:
        return Path(override_root).expanduser() / rel_suffix
    try:
        repo_root = Path(__file__).resolve().parents[2]
        storage_dir = repo_root / "src" / "backend" / "core" / "polaris_loop"
        if storage_dir.is_dir() and str(storage_dir) not in sys.path:
            sys.path.insert(0, str(storage_dir))
        from storage_layout import resolve_runtime_path  # type: ignore

        return Path(resolve_runtime_path(os.getcwd(), f"runtime/{rel_suffix}"))
    except Exception:
        return Path.home() / ".polaris" / "runtime" / rel_suffix

class EventsProtectionSystem:
    """events.jsonl 物理防护系统"""
    
    def __init__(self, events_file="events.jsonl"):
        self.events_file = Path(events_file)
        self.lock_file = Path(f"{events_file}.lock")
        self.hash_file = Path(f"{events_file}.hash")
        self.backup_dir = _resolve_runtime_artifact("agent_core/events_backup")
        
    def _acquire_lock(self):
        """获取文件锁"""
        if self.lock_file.exists():
            lock_age = time.time() - self.lock_file.stat().st_mtime
            if lock_age < 30:  # 30秒内的锁有效
                raise RuntimeError("events.jsonl is locked by another process")
            else:
                self.lock_file.unlink()  # 清理过期锁
        
        self.lock_file.write_text(str(os.getpid()))
        return True
    
    def _release_lock(self):
        """释放文件锁"""
        if self.lock_file.exists():
            self.lock_file.unlink()
    
    def _calculate_hash(self):
        """计算文件哈希"""
        if not self.events_file.exists():
            return None
        
        content = self.events_file.read_bytes()
        return hashlib.sha256(content).hexdigest()
    
    def _verify_integrity(self):
        """验证文件完整性"""
        if not self.hash_file.exists():
            return True  # 首次运行
        
        expected_hash = self.hash_file.read_text().strip()
        actual_hash = self._calculate_hash()
        
        if expected_hash != actual_hash:
            # 触发 S0 紧急中断
            self._trigger_emergency_stop("events.jsonl integrity compromised")
            return False
        
        return True
    
    def _trigger_emergency_stop(self, reason):
        """触发 S0 紧急中断"""
        emergency_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "EMERGENCY_STOP",
            "reason": reason,
            "action": "System halted - manual intervention required",
            "severity": "CRITICAL"
        }
        
        # 写入紧急日志
        with open("emergency_stop.jsonl", "a") as f:
            f.write(json.dumps(emergency_log) + "\n")
        
        raise RuntimeError(f"S0 EMERGENCY: {reason}")
    
    def _create_backup(self):
        """创建备份"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"events_{timestamp}.jsonl"
        
        if self.events_file.exists():
            backup_file.write_text(self.events_file.read_text())
    
    def append_event(self, event_data):
        """安全的追加事件"""
        try:
            self._acquire_lock()
            
            # 验证完整性
            if not self._verify_integrity():
                return False
            
            # 创建备份
            self._create_backup()
            
            # 追加新事件
            event_data["timestamp"] = datetime.utcnow().isoformat()
            event_data["entry_id"] = f"evt_{int(time.time() * 1000)}"
            
            with open(self.events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_data, ensure_ascii=False) + "\n")
            
            # 更新哈希
            new_hash = self._calculate_hash()
            self.hash_file.write_text(new_hash)
            
            return True
            
        except Exception as e:
            print(f"Failed to append event: {e}")
            return False
        finally:
            self._release_lock()
    
    def read_events(self, limit=None):
        """安全读取事件"""
        try:
            self._acquire_lock()
            
            if not self._verify_integrity():
                return []
            
            if not self.events_file.exists():
                return []
            
            events = []
            with open(self.events_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))
                        if limit and len(events) >= limit:
                            break
            
            return events
            
        except Exception as e:
            print(f"Failed to read events: {e}")
            return []
        finally:
            self._release_lock()
    
    def forbid_manual_edit(self):
        """禁止手动编辑的物理防护"""
        
        # 监控文件修改时间
        if self.events_file.exists():
            mtime = self.events_file.stat().st_mtime
            current_time = time.time()
            
            # 如果文件在最近5分钟内被修改，但没有通过我们的系统
            if current_time - mtime < 300:  # 5分钟
                if not self._was_modified_by_system():
                    self._trigger_emergency_stop(
                        "Manual edit detected on events.jsonl - "
                        "This violates AGENTS.md invariants"
                    )
    
    def _was_modified_by_system(self):
        """检查是否由系统修改"""
        # 简化实现：检查最近的锁文件活动
        if self.lock_file.exists():
            lock_age = time.time() - self.lock_file.stat().st_mtime
            return lock_age < 60  # 1分钟内的锁活动表示系统修改
        return False


# 全局事件保护系统实例
events_protection = EventsProtectionSystem()

# 导出的安全接口
def safe_append_event(event_data):
    """安全追加事件的唯一接口"""
    return events_protection.append_event(event_data)

def safe_read_events(limit=None):
    """安全读取事件的唯一接口"""
    return events_protection.read_events(limit)

def verify_events_integrity():
    """验证事件完整性"""
    return events_protection._verify_integrity()

if __name__ == "__main__":
    # 测试防护系统
    print("🛡️ Testing events protection system...")
    
    test_event = {
        "event_type": "TEST",
        "message": "Testing protection system"
    }
    
    if safe_append_event(test_event):
        print("✅ Event appended safely")
        
        events = safe_read_events(limit=5)
        print(f"📊 Read {len(events)} events")
        
        if verify_events_integrity():
            print("✅ Integrity verified")
        else:
            print("❌ Integrity check failed")
    else:
        print("❌ Failed to append event")
