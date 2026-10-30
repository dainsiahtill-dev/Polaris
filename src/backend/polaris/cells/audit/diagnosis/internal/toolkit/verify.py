"""Verification functions for audit chain integrity.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import hashlib
import hmac
import logging
from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.internal.usecases import AuditUseCaseFacade

logger = logging.getLogger(__name__)


def verify_chain(
    runtime_root: str,
) -> dict[str, Any]:
    """Verify audit chain integrity - 直接复用 AuditStore.verify_chain 语义。

    Args:
        runtime_root: Runtime 根目录

    Returns:
        验证结果字典
    """
    runtime_path = Path(runtime_root)
    audit_dir = runtime_path / "audit"

    if not audit_dir.exists():
        return {
            "chain_valid": False,
            "error": "Audit directory not found",
            "total_events": 0,
        }

    # 使用 Polaris audit facade（统一入口）
    try:
        facade = AuditUseCaseFacade(runtime_root=runtime_path.resolve())
        return facade.verify_chain()
    except Exception as e:
        logger.error("Failed to verify chain using audit facade: %s", e)
        raise RuntimeError("Audit chain verification failed") from e


def verify_file_integrity(
    file_path: str,
    expected_hash: str | None = None,
    algorithm: str = "sha256",
) -> dict[str, Any]:
    """Verify file integrity.

    Args:
        file_path: 文件路径
        expected_hash: 期望的哈希值
        algorithm: 哈希算法 (sha256, sha1, md5)

    Returns:
        验证结果字典
    """
    path = Path(file_path)

    if not path.exists():
        return {
            "valid": False,
            "error": "File not found",
            "file_path": file_path,
        }

    # Calculate hash - only SHA256 allowed for security
    if algorithm == "sha256":
        hasher = hashlib.sha256()
    else:
        return {
            "valid": False,
            "error": f"Unsupported algorithm: {algorithm}",
            "file_path": file_path,
        }

    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)

    actual_hash = hasher.hexdigest()

    if expected_hash is None:
        return {
            "valid": True,
            "file_path": file_path,
            "algorithm": algorithm,
            "hash": actual_hash,
        }

    return {
        "valid": actual_hash.lower() == expected_hash.lower(),
        "file_path": file_path,
        "algorithm": algorithm,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
    }


def verify_hmac_signature(
    payload: str,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC signature.

    Args:
        payload: 数据载荷
        signature: 签名
        secret: 密钥

    Returns:
        是否验证通过
    """
    expected = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
