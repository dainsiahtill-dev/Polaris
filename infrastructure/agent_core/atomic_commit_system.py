#!/usr/bin/env python3
"""
原子提交系统 - 具象化的回滚路径实现
符合 AGENTS.md v2.2 中的 Atomic Commit/Snapshot 要求
"""

import os
import json
import shutil
import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


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

class AtomicCommitSystem:
    """原子提交系统"""
    
    def __init__(self):
        self.snapshot_dir = _resolve_runtime_artifact("agent_core/snapshots")
        self.rollback_dir = _resolve_runtime_artifact("agent_core/rollback")
        self.commit_log = _resolve_runtime_artifact("agent_core/commit_log.jsonl")
        
        # 创建必要目录
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.rollback_dir.mkdir(parents=True, exist_ok=True)
    
    def create_pre_implementation_snapshot(self, affected_files: List[str], 
                                        blueprint_id: str) -> Dict[str, Any]:
        """创建实施前快照"""
        
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"pre_{blueprint_id}_{timestamp}"
        
        snapshot = {
            "snapshot_id": snapshot_id,
            "type": "pre_implementation",
            "blueprint_id": blueprint_id,
            "timestamp": datetime.utcnow().isoformat(),
            "git_sha": self._get_current_git_sha(),
            "affected_files": [],
            "file_hashes": {},
            "backup_paths": {}
        }
        
        # 备份每个受影响的文件
        for file_path in affected_files:
            if Path(file_path).exists():
                backup_info = self._backup_file(file_path, snapshot_id)
                snapshot["affected_files"].append(file_path)
                snapshot["file_hashes"][file_path] = backup_info["hash"]
                snapshot["backup_paths"][file_path] = backup_info["backup_path"]
        
        # 保存快照元数据
        snapshot_file = self.snapshot_dir / f"{snapshot_id}.json"
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        
        # 记录到提交日志
        self._log_commit(snapshot)
        
        return snapshot
    
    def create_post_implementation_snapshot(self, blueprint_id: str) -> Dict[str, Any]:
        """创建实施后快照"""
        
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"post_{blueprint_id}_{timestamp}"
        
        snapshot = {
            "snapshot_id": snapshot_id,
            "type": "post_implementation",
            "blueprint_id": blueprint_id,
            "timestamp": datetime.utcnow().isoformat(),
            "git_sha": self._get_current_git_sha(),
            "implementation_changes": self._detect_changes_since_pre_snapshot(blueprint_id)
        }
        
        # 保存快照元数据
        snapshot_file = self.snapshot_dir / f"{snapshot_id}.json"
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        
        # 记录到提交日志
        self._log_commit(snapshot)
        
        return snapshot
    
    def _backup_file(self, file_path: str, snapshot_id: str) -> Dict[str, Any]:
        """备份单个文件"""
        
        source_path = Path(file_path)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{timestamp}_{source_path.name}"
        backup_path = self.rollback_dir / backup_filename
        
        # 复制文件
        shutil.copy2(source_path, backup_path)
        
        # 计算文件哈希
        file_hash = self._calculate_file_hash(source_path)
        
        return {
            "backup_path": str(backup_path),
            "hash": file_hash,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件哈希"""
        with open(file_path, "rb") as f:
            content = f.read()
        return hashlib.sha256(content).hexdigest()
    
    def _get_current_git_sha(self) -> Optional[str]:
        """获取当前 Git SHA"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    
    def _detect_changes_since_pre_snapshot(self, blueprint_id: str) -> List[Dict[str, Any]]:
        """检测自预快照以来的变更"""
        
        # 查找对应的预快照
        pre_snapshot = self._find_pre_snapshot(blueprint_id)
        if not pre_snapshot:
            return []
        
        changes = []
        
        for file_path in pre_snapshot["affected_files"]:
            if Path(file_path).exists():
                current_hash = self._calculate_file_hash(Path(file_path))
                original_hash = pre_snapshot["file_hashes"][file_path]
                
                if current_hash != original_hash:
                    changes.append({
                        "file_path": file_path,
                        "change_type": "modified",
                        "original_hash": original_hash,
                        "current_hash": current_hash
                    })
            else:
                changes.append({
                    "file_path": file_path,
                    "change_type": "deleted",
                    "original_hash": pre_snapshot["file_hashes"][file_path]
                })
        
        return changes
    
    def _find_pre_snapshot(self, blueprint_id: str) -> Optional[Dict[str, Any]]:
        """查找预快照"""
        
        for snapshot_file in self.snapshot_dir.glob(f"pre_{blueprint_id}_*.json"):
            with open(snapshot_file, "r", encoding="utf-8") as f:
                return json.load(f)
        
        return None
    
    def _log_commit(self, snapshot: Dict[str, Any]):
        """记录提交日志"""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "snapshot_id": snapshot["snapshot_id"],
            "type": snapshot["type"],
            "blueprint_id": snapshot["blueprint_id"],
            "git_sha": snapshot["git_sha"]
        }
        
        with open(self.commit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    def rollback_to_snapshot(self, snapshot_id: str, reason: str = "Manual rollback") -> Dict[str, Any]:
        """回滚到指定快照"""
        
        snapshot_file = self.snapshot_dir / f"{snapshot_id}.json"
        if not snapshot_file.exists():
            return {
                "success": False,
                "reason": f"Snapshot {snapshot_id} not found",
                "rollback_type": "snapshot"
            }
        
        with open(snapshot_file, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        
        if snapshot["type"] != "pre_implementation":
            return {
                "success": False,
                "reason": "Can only rollback to pre-implementation snapshots",
                "rollback_type": "snapshot"
            }
        
        # 执行回滚
        rollback_results = []
        
        for file_path, backup_info in snapshot["backup_paths"].items():
            backup_path = Path(backup_info)
            
            if backup_path.exists():
                # 恢复文件
                shutil.copy2(backup_path, file_path)
                rollback_results.append({
                    "file_path": file_path,
                    "status": "restored",
                    "backup_path": str(backup_path)
                })
            else:
                rollback_results.append({
                    "file_path": file_path,
                    "status": "failed",
                    "reason": "Backup file not found"
                })
        
        # 记录回滚操作
        rollback_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "snapshot_id": snapshot_id,
            "reason": reason,
            "rollback_results": rollback_results,
            "rollback_type": "snapshot"
        }
        
        with open(self.commit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rollback_record, ensure_ascii=False) + "\n")
        
        return {
            "success": True,
            "snapshot_id": snapshot_id,
            "rollback_results": rollback_results,
            "rollback_type": "snapshot"
        }
    
    def rollback_to_git_sha(self, git_sha: str, reason: str = "Git rollback") -> Dict[str, Any]:
        """回滚到 Git SHA"""
        
        try:
            # 检查 Git SHA 是否存在
            subprocess.run(
                ["git", "cat-file", "-t", git_sha],
                capture_output=True,
                text=True,
                check=True
            )
            
            # 执行 git reset
            subprocess.run(
                ["git", "reset", "--hard", git_sha],
                capture_output=True,
                text=True,
                check=True
            )
            
            # 记录回滚操作
            rollback_record = {
                "timestamp": datetime.utcnow().isoformat(),
                "git_sha": git_sha,
                "reason": reason,
                "rollback_type": "git"
            }
            
            with open(self.commit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rollback_record, ensure_ascii=False) + "\n")
            
            return {
                "success": True,
                "git_sha": git_sha,
                "rollback_type": "git"
            }
            
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "reason": f"Git operation failed: {e.stderr}",
                "rollback_type": "git"
            }
    
    def get_available_rollback_points(self, blueprint_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取可用的回滚点"""
        
        rollback_points = []
        
        # 扫描快照
        for snapshot_file in self.snapshot_dir.glob("*.json"):
            try:
                with open(snapshot_file, "r", encoding="utf-8") as f:
                    snapshot = json.load(f)
                
                if blueprint_id and snapshot["blueprint_id"] != blueprint_id:
                    continue
                
                rollback_points.append({
                    "type": "snapshot",
                    "snapshot_id": snapshot["snapshot_id"],
                    "blueprint_id": snapshot["blueprint_id"],
                    "timestamp": snapshot["timestamp"],
                    "git_sha": snapshot["git_sha"],
                    "affected_files_count": len(snapshot.get("affected_files", []))
                })
                
            except Exception as e:
                print(f"Error reading snapshot {snapshot_file}: {e}")
        
        # 添加最近的 Git 提交
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                capture_output=True,
                text=True,
                check=True
            )
            
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 1)
                    git_sha = parts[0]
                    commit_msg = parts[1] if len(parts) > 1 else ""
                    
                    rollback_points.append({
                        "type": "git",
                        "git_sha": git_sha,
                        "commit_message": commit_msg,
                        "timestamp": "git_timestamp"  # 可以通过 git log 获取详细时间
                    })
        
        except subprocess.CalledProcessError:
            pass  # Git 不可用时忽略
        
        # 按时间排序
        rollback_points.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        return rollback_points
    
    def audit_rollback_history(self) -> Dict[str, Any]:
        """审计回滚历史"""
        
        if not self.commit_log.exists():
            return {"audit_possible": False, "reason": "No commit log found"}
        
        rollbacks = []
        with open(self.commit_log, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if "rollback_type" in record:
                        rollbacks.append(record)
        
        return {
            "audit_possible": True,
            "total_rollbacks": len(rollbacks),
            "rollback_types": {},
            "recent_rollbacks": rollbacks[-5:] if rollbacks else []
        }


# 全局原子提交系统实例
atomic_commit = AtomicCommitSystem()

# 导出的安全接口
def create_atomic_snapshot(affected_files: List[str], blueprint_id: str) -> Dict[str, Any]:
    """创建原子快照的统一接口"""
    return atomic_commit.create_pre_implementation_snapshot(affected_files, blueprint_id)

def execute_atomic_rollback(snapshot_id: str, reason: str = "Implementation failed") -> Dict[str, Any]:
    """执行原子回滚的统一接口"""
    return atomic_commit.rollback_to_snapshot(snapshot_id, reason)

if __name__ == "__main__":
    # 测试原子提交系统
    print("🔄 Testing atomic commit system...")
    
    # 创建测试文件
    test_file = "test_atomic.txt"
    with open(test_file, "w") as f:
        f.write("Original content")
    
    # 创建快照
    snapshot = create_atomic_snapshot([test_file], "test_blueprint")
    print(f"Created snapshot: {snapshot['snapshot_id']}")
    
    # 修改文件
    with open(test_file, "w") as f:
        f.write("Modified content")
    
    # 回滚
    rollback = execute_atomic_rollback(snapshot["snapshot_id"], "Test rollback")
    print(f"Rollback result: {rollback['success']}")
    
    # 清理
    if os.path.exists(test_file):
        os.remove(test_file)
