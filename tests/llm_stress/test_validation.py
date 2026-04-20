"""测试新的角色输出验证功能"""

import sys
from pathlib import Path

# 添加backend到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "backend"))

from app.llm.usecases.role_dialogue import (
    RoleOutputParser,
    RoleOutputQualityChecker,
    validate_and_parse_role_output,
)


def test_pm_parsing():
    """测试PM输出解析"""
    print("=" * 60)
    print("测试 PM 输出解析")
    print("=" * 60)

    pm_output = '''
<thinking>分析需求，拆解任务...</thinking>
<output>
```json
{
  "tasks": [
    {
      "id": "TASK-001",
      "title": "实现用户登录",
      "description": "添加JWT认证登录接口",
      "target_files": ["src/auth.py"],
      "acceptance_criteria": ["支持JWT", "密码验证"],
      "priority": "high",
      "phase": "core"
    }
  ],
  "analysis": {
    "total_tasks": 1,
    "risk_level": "low"
  }
}
```
</output>
'''

    result = validate_and_parse_role_output('pm', pm_output)
    print(f"Success: {result['success']}")
    print(f"Quality Score: {result['quality_score']:.1f}")
    print(f"Errors: {result['errors']}")
    print(f"Suggestions: {result['suggestions']}")

    # 验证数据提取
    if result['data']:
        tasks = result['data'].get('tasks', [])
        print(f"Extracted {len(tasks)} tasks")
        for task in tasks:
            print(f"  - {task.get('id')}: {task.get('title')}")

    return result['success']


def test_director_patch_extraction():
    """测试Director补丁提取"""
    print("\n" + "=" * 60)
    print("测试 Director 补丁提取")
    print("=" * 60)

    director_output = '''
<thinking>生成代码补丁...</thinking>
<output>
PATCH_FILE: src/auth.py
<<<<<<< SEARCH
def old_func():
    pass
=======
def new_func():
    return True
>>>>>>> REPLACE
END PATCH_FILE

PATCH_FILE: src/models.py
<<<<<<< SEARCH
class User:
    name: str
=======
class User:
    name: str
    email: str
>>>>>>> REPLACE
END PATCH_FILE
</output>
'''

    patches, errors = RoleOutputParser.extract_patch_blocks(director_output)
    print(f"Patches found: {len(patches)}")
    print(f"Errors: {errors}")

    for i, patch in enumerate(patches, 1):
        print(f"  Patch {i}: {patch['file']}")
        print(f"    Search: {patch['search'][:50]}...")
        print(f"    Replace: {patch['replace'][:50]}...")

    return len(patches) == 2 and len(errors) == 0


def test_security_checks():
    """测试安全检查"""
    print("\n" + "=" * 60)
    print("测试安全检查")
    print("=" * 60)

    # 测试路径遍历攻击
    unsafe_output = '''
PATCH_FILE: ../../../etc/passwd
<<<<<<< SEARCH
root:x:0:0:root:/root:/bin/bash
=======
root:x:0:0:root:/root:/bin/bash
>>>>>>> REPLACE
END PATCH_FILE
'''

    patches, errors = RoleOutputParser.extract_patch_blocks(unsafe_output)
    print(f"Path traversal test:")
    print(f"  Patches found: {len(patches)} (should be 0)")
    print(f"  Security errors: {errors}")

    security_pass = len(patches) == 0 and len(errors) > 0

    # 测试Director危险代码
    director_dangerous = '''
PATCH_FILE: src/hack.py
<<<<<<< SEARCH
# empty
=======
import os
eval(user_input)
>>>>>>> REPLACE
END PATCH_FILE
'''

    score, suggestions = RoleOutputQualityChecker._check_director_output(
        director_dangerous, None
    )
    print(f"\nDangerous code test:")
    print(f"  Score: {score} (should be low)")
    print(f"  Warnings: {suggestions}")

    return security_pass and score < 50


def test_qa_validation():
    """测试QA输出验证"""
    print("\n" + "=" * 60)
    print("测试 QA 输出验证")
    print("=" * 60)

    qa_output = '''
<output>
```json
{
  "review_id": "REV-001",
  "verdict": "FAIL",
  "confidence": "high",
  "summary": "发现安全问题",
  "findings": [
    {
      "severity": "critical",
      "category": "security",
      "location": "auth.py:45",
      "description": "SQL注入漏洞",
      "evidence": "cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')",
      "recommendation": "使用参数化查询"
    }
  ],
  "metrics": {
    "test_coverage": "75%",
    "security_issues": 1
  },
  "checklist_results": {
    "functional_completeness": true,
    "security_check_passed": false
  },
  "risks": [
    {
      "level": "high",
      "description": "数据泄露风险",
      "probability": "likely"
    }
  ],
  "recommendations": ["立即修复SQL注入问题"]
}
```
</output>
'''

    result = validate_and_parse_role_output('qa', qa_output)
    print(f"Success: {result['success']}")
    print(f"Quality Score: {result['quality_score']:.1f}")

    if result['data']:
        print(f"Verdict: {result['data'].get('verdict')}")
        print(f"Findings: {len(result['data'].get('findings', []))}")

    return result['success'] and result['quality_score'] > 80


def test_architect_output():
    """测试Architect输出验证"""
    print("\n" + "=" * 60)
    print("测试 Architect 输出验证")
    print("=" * 60)

    architect_output = '''
<output>
## 1. 架构概览
本系统采用微服务架构，支持水平扩展。

## 2. 技术栈选型
| 层级 | 技术选型 | 选型理由 |
|-----|---------|---------|
| 数据层 | PostgreSQL | ACID保障 |
| 服务层 | FastAPI | 高性能异步 |

## 3. 模块设计
- 认证模块：JWT实现
- 数据模块：ORM封装

## 4. 非功能需求
- 性能：支持1000 QPS
- 可用性：99.9%

## 5. 风险评估
| 风险 | 概率 | 影响 | 缓解措施 |
|-----|-----|-----|---------|
| 数据库单点 | 中 | 高 | 主从复制 |

## 6. 实施建议
分三期实施：基础框架、核心功能、优化增强
</output>
'''

    is_valid, data, errors = RoleOutputParser.validate_role_output('architect', architect_output)
    print(f"Valid: {is_valid}")
    print(f"Errors: {errors}")

    score, suggestions = RoleOutputQualityChecker.check_output('architect', architect_output, data)
    print(f"Quality Score: {score:.1f}")
    print(f"Suggestions: {suggestions}")

    return is_valid


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Polaris 角色输出验证测试套件")
    print("=" * 60)

    results = []

    results.append(("PM Parsing", test_pm_parsing()))
    results.append(("Director Patch", test_director_patch_extraction()))
    results.append(("Security Checks", test_security_checks()))
    results.append(("QA Validation", test_qa_validation()))
    results.append(("Architect Output", test_architect_output()))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\n总计: {passed}/{total} 通过")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
