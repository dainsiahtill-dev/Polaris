#!/usr/bin/env python3
"""
确定性验证系统 - 对抗幻觉证据
实现 AGENTS.md v2.2 中的 Deterministic Verification 要求
"""

import json
import hashlib
import secrets
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


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

class DeterministicVerification:
    """确定性验证系统"""
    
    def __init__(self):
        self.verification_log = _resolve_runtime_artifact(
            "agent_core/verification_log.jsonl"
        )
        self.verification_log.parent.mkdir(parents=True, exist_ok=True)
        self.active_challenges = {}
    
    def generate_verification_nonce(self, operation: str) -> str:
        """生成验证用的随机数"""
        nonce = secrets.token_hex(16)
        timestamp = datetime.utcnow().isoformat()
        
        challenge = {
            "operation": operation,
            "nonce": nonce,
            "timestamp": timestamp,
            "challenge_hash": self._hash_challenge(nonce, timestamp, operation)
        }
        
        self.active_challenges[operation] = challenge
        return nonce
    
    def _hash_challenge(self, nonce: str, timestamp: str, operation: str) -> str:
        """生成挑战哈希"""
        content = f"{nonce}:{timestamp}:{operation}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def verify_evidence(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """验证证据的确定性"""
        
        operation = evidence.get("operation", "unknown")
        timestamp = evidence.get("timestamp", "")
        result = evidence.get("result", "")
        
        # 检查是否有对应的挑战
        if operation not in self.active_challenges:
            return {
                "verified": False,
                "reason": "No active challenge for this operation",
                "verification_type": "deterministic"
            }
        
        challenge = self.active_challenges[operation]
        
        # 验证时间戳合理性
        if not self._verify_timestamp_reasonable(timestamp, challenge["timestamp"]):
            return {
                "verified": False,
                "reason": "Timestamp not reasonable (too old or future)",
                "verification_type": "deterministic"
            }
        
        # 生成确定性验证标记
        verification_token = self._generate_verification_token(
            challenge["nonce"], 
            timestamp, 
            result, 
            operation
        )
        
        # 记录验证日志
        verification_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "operation": operation,
            "evidence_timestamp": timestamp,
            "challenge_nonce": challenge["nonce"],
            "verification_token": verification_token,
            "verified": True,
            "verification_type": "deterministic"
        }
        
        self._log_verification(verification_record)
        
        # 清理已使用的挑战
        del self.active_challenges[operation]
        
        return {
            "verified": True,
            "verification_token": verification_token,
            "verification_type": "deterministic",
            "challenge_nonce": challenge["nonce"]
        }
    
    def _verify_timestamp_reasonable(self, evidence_ts: str, challenge_ts: str) -> bool:
        """验证时间戳合理性"""
        try:
            evidence_time = datetime.fromisoformat(evidence_ts.replace('Z', '+00:00'))
            challenge_time = datetime.fromisoformat(challenge_ts.replace('Z', '+00:00'))
            current_time = datetime.utcnow()
            
            # 证据时间应该在挑战时间之后，且不超过5分钟
            time_diff = evidence_time - challenge_time
            total_diff = current_time - challenge_time
            
            return (time_diff.total_seconds() >= 0 and 
                   total_diff.total_seconds() <= 300)  # 5分钟窗口
            
        except Exception:
            return False
    
    def _generate_verification_token(self, nonce: str, timestamp: str, 
                                   result: str, operation: str) -> str:
        """生成验证令牌"""
        content = f"{nonce}:{timestamp}:{result}:{operation}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _log_verification(self, verification_record: Dict[str, Any]):
        """记录验证日志"""
        with open(self.verification_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(verification_record, ensure_ascii=False) + "\n")
    
    def create_deterministic_test(self, test_command: str) -> Dict[str, Any]:
        """创建确定性测试"""
        nonce = self.generate_verification_nonce("test_execution")
        
        test_spec = {
            "command": test_command,
            "verification_nonce": nonce,
            "timestamp": datetime.utcnow().isoformat(),
            "expected_evidence_format": {
                "must_include_nonce": True,
                "must_include_timestamp": True,
                "must_include_execution_time": True,
                "must_be_real_time": True
            }
        }
        
        return test_spec
    
    def validate_test_output(self, test_spec: Dict[str, Any], 
                           actual_output: str, execution_time: float) -> Dict[str, Any]:
        """验证测试输出的确定性"""
        
        nonce = test_spec["verification_nonce"]
        
        # 检查输出是否包含 nonce
        if nonce not in actual_output:
            return {
                "valid": False,
                "reason": f"Output doesn't contain verification nonce: {nonce}",
                "deterministic": False
            }
        
        # 检查执行时间合理性
        if execution_time <= 0 or execution_time > 300:  # 5分钟上限
            return {
                "valid": False,
                "reason": f"Unreasonable execution time: {execution_time}s",
                "deterministic": False
            }
        
        # 生成确定性验证
        evidence = {
            "operation": "test_execution",
            "timestamp": datetime.utcnow().isoformat(),
            "result": actual_output,
            "execution_time_ms": execution_time * 1000,
            "verification_nonce": nonce
        }
        
        verification = self.verify_evidence(evidence)
        
        return {
            "valid": verification["verified"],
            "verification_token": verification.get("verification_token"),
            "deterministic": True,
            "evidence": evidence
        }
    
    def audit_evidence_chain(self) -> Dict[str, Any]:
        """审计证据链"""
        if not self.verification_log.exists():
            return {"audit_possible": False, "reason": "No verification log found"}
        
        verifications = []
        with open(self.verification_log, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    verifications.append(json.loads(line))
        
        # 检查验证记录的完整性
        audit_result: Dict[str, Any] = {
            "audit_possible": True,
            "total_verifications": len(verifications),
            "verification_types": {},
            "time_span": {},
            "integrity_issues": []
        }
        
        if verifications:
            timestamps = [v["timestamp"] for v in verifications]
            audit_result["time_span"] = {
                "first": min(timestamps),
                "last": max(timestamps)
            }
            
            # 统计验证类型
            for v in verifications:
                v_type = v.get("verification_type", "unknown")
                audit_result["verification_types"][v_type] = \
                    audit_result["verification_types"].get(v_type, 0) + 1
        
        return audit_result


class EvidenceValidator:
    """证据验证器 - 防止幻觉证据"""
    
    def __init__(self):
        self.deterministic_verifier = DeterministicVerification()
    
    def validate_command_output(self, command: str, output: str, 
                              execution_time: float) -> Dict[str, Any]:
        """验证命令输出的真实性"""
        
        # 1. 基础合理性检查
        basic_check = self._basic_output_validation(command, output)
        if not basic_check["valid"]:
            return basic_check
        
        # 2. 确定性验证
        test_spec = self.deterministic_verifier.create_deterministic_test(command)
        validation = self.deterministic_verifier.validate_test_output(
            test_spec, output, execution_time
        )
        
        return {
            "valid": validation["valid"],
            "deterministic": validation["deterministic"],
            "verification_token": validation.get("verification_token"),
            "basic_check": basic_check,
            "evidence_type": "command_output"
        }
    
    def _basic_output_validation(self, command: str, output: str) -> Dict[str, Any]:
        """基础输出验证"""
        
        # 检查输出长度合理性
        if len(output) > 1000000:  # 1MB 限制
            return {
                "valid": False,
                "reason": "Output too large, possibly fabricated",
                "evidence_type": "basic_validation"
            }
        
        # 检查输出内容合理性
        suspicious_patterns = [
            "I cannot",
            "As an AI",
            "I'm sorry",
            "I don't have",
            "I cannot access"
        ]
        
        for pattern in suspicious_patterns:
            if pattern in output and "test" not in command.lower():
                return {
                    "valid": False,
                    "reason": f"Output contains AI-like response: {pattern}",
                    "evidence_type": "basic_validation"
                }
        
        return {
            "valid": True,
            "reason": "Basic validation passed",
            "evidence_type": "basic_validation"
        }


# 全局验证器实例
evidence_validator = EvidenceValidator()

# 导出的安全接口
def validate_evidence_deterministic(command: str, output: str, 
                                   execution_time: float) -> Dict[str, Any]:
    """确定性证据验证的统一接口"""
    return evidence_validator.validate_command_output(command, output, execution_time)

if __name__ == "__main__":
    # 测试确定性验证
    print("🔍 Testing deterministic verification...")
    
    # 模拟测试
    test_command = "echo 'test output'"
    test_output = "test output with nonce: abc123"
    test_time = 0.1
    
    result = validate_evidence_deterministic(test_command, test_output, test_time)
    print(f"Validation result: {result}")
    
    # 审计证据链
    audit = DeterministicVerification().audit_evidence_chain()
    print(f"Audit result: {audit}")
