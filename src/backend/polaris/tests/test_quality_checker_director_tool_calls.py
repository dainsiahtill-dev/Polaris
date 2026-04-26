from __future__ import annotations

from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker
from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES
from polaris.cells.roles.profile.internal.schema import profile_from_dict


def _load_director_profile():
    for item in BUILTIN_PROFILES:
        if str(item.get("role_id") or "").strip().lower() == "director":
            return profile_from_dict(item)
    raise AssertionError("director profile not found")


def test_director_quality_checker_accepts_tool_calls_without_patch() -> None:
    checker = QualityChecker()
    profile = _load_director_profile()
    content = """
<thinking>
先查看当前目录结构。
</thinking>
<output>
[TOOL_NAME]
execute_command command: "dir /b"
[/TOOL_NAME]
</output>
""".strip()

    result = checker.validate_output(content, profile)

    assert result.success is True
    assert result.quality_score >= 60
    assert isinstance(result.data, dict)
    tool_calls = result.data.get("tool_calls")
    assert isinstance(tool_calls, list)
    assert len(tool_calls) == 1
    assert tool_calls[0].get("name") == "execute_command"


def test_director_quality_checker_rejects_execution_status_without_patch_or_tools() -> None:
    checker = QualityChecker()
    profile = _load_director_profile()
    content = """
```json
{
  "execution_status": "success",
  "actions_taken": [
    {
      "type": "create",
      "file": "src/fastapi_entrypoint.py",
      "status": "success",
      "details": "reported only"
    }
  ]
}
```
""".strip()

    result = checker.validate_output(content, profile)

    assert result.success is False
    assert result.quality_score < 60


def test_director_quality_checker_accepts_patch_file_direct_content() -> None:
    checker = QualityChecker()
    profile = _load_director_profile()
    content = """
PATCH_FILE: src/expense/model.py
from dataclasses import dataclass


@dataclass
class Expense:
    amount: float
END PATCH_FILE
""".strip()

    result = checker.validate_output(content, profile)

    assert result.success is True
    assert isinstance(result.data, dict)
    patches = result.data.get("patches")
    assert isinstance(patches, list)
    assert len(patches) >= 1
    assert patches[0].get("file") == "src/expense/model.py"


def test_director_quality_checker_accepts_markdown_file_blocks() -> None:
    checker = QualityChecker()
    profile = _load_director_profile()
    content = """
src/expense/repository.py
```python
def save_expense(amount: float) -> float:
    if amount <= 0:
        raise ValueError("amount must be positive")
    return amount
```
""".strip()

    result = checker.validate_output(content, profile)

    assert result.success is True
    assert isinstance(result.data, dict)
    patches = result.data.get("patches")
    assert isinstance(patches, list)
    assert len(patches) >= 1
    assert patches[0].get("file") == "src/expense/repository.py"


def test_director_quality_checker_handles_json_array_without_crashing() -> None:
    checker = QualityChecker()
    profile = _load_director_profile()
    content = """
```json
[
  {
    "execution_status": "success"
  }
]
```
""".strip()

    result = checker.validate_output(content, profile)

    assert result.success is False
    assert result.quality_score < 60
