"""Frozen legacy parser projections and cold public use without Director execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.turn_engine import assistant_raw_content
from polaris.cells.roles.kernel.public import OutputParser, QualityChecker

# Observed on the original two-file implementation before the dependency cut.
# Fixed inputs and expected projections intentionally include existing lenient
# quality fallbacks; this refactor grants no apply or execution authority.
_CORPUS: list[dict[str, Any]] = [
    {
        "name": "search_replace",
        "content": "PATCH_FILE: src/a.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行", "file": "src/a.py"}],
            "quality_unified": [{"file": "src/a.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "quality_all": [{"file": "src/a.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [],
        },
    },
    {
        "name": "full_file",
        "content": "FILE: src/full.py\nprint('完整')\nEND FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [{"file": "src/full.py", "search": "", "replace": "print('完整')"}],
            "quality_all": [{"file": "src/full.py", "search": "", "replace": "print('完整')"}],
            "native_calls": [],
        },
    },
    {
        "name": "create",
        "content": "CREATE: src/new.py\nprint('新建')\nEND FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [{"file": "src/new.py", "search": "", "replace": "print('新建')"}],
            "quality_all": [{"file": "src/new.py", "search": "", "replace": "print('新建')"}],
            "native_calls": [],
        },
    },
    {
        "name": "delete",
        "content": "DELETE_FILE: src/dead.py\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [{"file": "src/dead.py", "search": "", "replace": ""}],
            "quality_all": [{"file": "src/dead.py", "search": "", "replace": ""}],
            "native_calls": [],
        },
    },
    {
        "name": "mixed",
        "content": "DELETE_FILE: src/dead.py\n"
        "PATCH_FILE: src/a.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
        "FILE: src/full.py\n"
        "print('完整')\n"
        "END FILE\n"
        "CREATE: src/new.py\n"
        "print('新建')\n"
        "END FILE\n",
        "expected": {
            "search_replace": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行", "file": "src/a.py"}],
            "quality_unified": [
                {"file": "src/dead.py", "search": "", "replace": ""},
                {"file": "src/a.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"},
                {"file": "src/new.py", "search": "", "replace": "print('新建')"},
                {"file": "src/full.py", "search": "", "replace": "print('完整')"},
            ],
            "quality_all": [
                {"file": "src/dead.py", "search": "", "replace": ""},
                {"file": "src/a.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"},
                {"file": "src/new.py", "search": "", "replace": "print('新建')"},
                {"file": "src/full.py", "search": "", "replace": "print('完整')"},
            ],
            "native_calls": [],
        },
    },
    {
        "name": "pathless",
        "content": "<<<<<<< SEARCH\n旧值\n=======\n新值\n>>>>>>> REPLACE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"search": "旧值", "replace": "新值"}],
            "native_calls": [],
        },
    },
    {
        "name": "absolute",
        "content": "PATCH_FILE: /tmp/outside.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [],
        },
    },
    {
        "name": "windows",
        "content": "PATCH_FILE: C:\\outside.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [],
        },
    },
    {
        "name": "traversal",
        "content": "PATCH_FILE: ../outside.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [],
        },
    },
    {
        "name": "nul",
        "content": "PATCH_FILE: src/bad\x00.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [],
        },
    },
    {
        "name": "malformed",
        "content": "PATCH_FILE src/a.py\n<<<<<<< SEARCH\nold\n=======\nnew\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [{"file": "src/a.py", "search": "", "replace": "old\nnew"}],
            "quality_all": [{"file": "src/a.py", "search": "", "replace": "old\nnew"}],
            "native_calls": [],
        },
    },
    {
        "name": "torn",
        "content": "PATCH_FILE: src/a.py\n<<<<<<< SEARCH\nold\n=======\nnew\nEND PATCH_FILE\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [{"file": "src/a.py", "search": "", "replace": "old\nnew"}],
            "quality_all": [{"file": "src/a.py", "search": "", "replace": "old\nnew"}],
            "native_calls": [],
        },
    },
    {
        "name": "markdown",
        "content": "# src/fallback.py\n\n```python\nprint('回退')\n```\n",
        "expected": {
            "search_replace": None,
            "quality_unified": [],
            "quality_all": [{"file": "src/fallback.py", "search": "", "replace": "print('回退')"}],
            "native_calls": [],
        },
    },
    {
        "name": "empty",
        "content": "",
        "expected": {"search_replace": None, "quality_unified": [], "quality_all": [], "native_calls": []},
    },
    {
        "name": "mixed_native",
        "content": "PATCH_FILE: src/native.py\n"
        "<<<<<<< SEARCH\n"
        "旧值 = 1\n"
        "第二行\n"
        "=======\n"
        "新值 = 2\n"
        "第三行\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n",
        "expected": {
            "search_replace": [{"search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行", "file": "src/native.py"}],
            "quality_unified": [{"file": "src/native.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "quality_all": [{"file": "src/native.py", "search": "旧值 = 1\n第二行", "replace": "新值 = 2\n第三行"}],
            "native_calls": [["read_file", {"file": "src/a.py"}]],
        },
    },
]


@pytest.mark.parametrize("row", _CORPUS, ids=[row["name"] for row in _CORPUS])
def test_public_role_parser_keeps_original_corpus_projection(row: dict[str, Any]) -> None:
    parser = OutputParser()
    checker = QualityChecker()
    content = row["content"]
    expected = row["expected"]
    assert parser.extract_search_replace(content) == expected["search_replace"]
    assert checker._extract_patch_operations_from_unified_parser(content) == expected["quality_unified"]
    assert checker._extract_director_patches(content) == expected["quality_all"]
    native = (
        [{"id": "read-1", "type": "function", "function": {"name": "read_file", "arguments": '{"file":"src/a.py"}'}}]
        if row["name"] == "mixed_native"
        else None
    )
    calls = parser.parse_execution_tool_calls(
        assistant_raw_content(content),
        allowed_tool_names=["read_file"],
        native_tool_calls=native,
        native_provider="openai" if native else "auto",
    )
    assert [[call.tool, dict(call.args)] for call in calls] == expected["native_calls"]


_COLD_SCRIPT = r"""import importlib.abc
import json
import sys

class DenyDirectorExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "polaris.cells.director.execution" or fullname.startswith("polaris.cells.director.execution."):
            raise ImportError("Director execution must not load for generic role protocol parsing: " + fullname)
        return None

sys.meta_path.insert(0, DenyDirectorExecution())
from polaris.cells.roles.kernel.public import OutputParser, QualityChecker
rows = json.loads(sys.stdin.read())
parser = OutputParser()
checker = QualityChecker()
result = []
for row in rows:
    text = row["content"]
    if sys.argv[1] == "output":
        result.append(parser.extract_search_replace(text))
    else:
        result.append([checker._extract_patch_operations_from_unified_parser(text), checker._extract_director_patches(text)])
assert not any(name == "polaris.cells.director.execution" or name.startswith("polaris.cells.director.execution.") for name in sys.modules)
print("PARSER_CUT_RESULT=" + json.dumps(result, ensure_ascii=False))
"""


@pytest.mark.parametrize("consumer", ("output", "quality"))
def test_cold_public_parser_does_not_import_director_execution(consumer: str) -> None:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", _COLD_SCRIPT, consumer],
        input=json.dumps(_CORPUS, ensure_ascii=False),
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = next(
        line.removeprefix("PARSER_CUT_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PARSER_CUT_RESULT=")
    )
    expected = [
        row["expected"]["search_replace"]
        if consumer == "output"
        else [row["expected"]["quality_unified"], row["expected"]["quality_all"]]
        for row in _CORPUS
    ]
    assert json.loads(payload) == expected
