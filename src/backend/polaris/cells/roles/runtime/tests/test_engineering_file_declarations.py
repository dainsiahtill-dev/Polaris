"""`_declarations` 必须给出真实符号名，不是类型关键字。

实测（L1-06 工作区，20 个源文件）：符号总数 72，其中 25 个（34.7%）是类型
关键字，14/20 个文件受影响；`const std::string s` 抓到 `std`、
`const unsigned x` 抓到 `unsigned`。索引给了地址却没给可回忆的内容。
"""

from __future__ import annotations

from polaris.cells.roles.runtime.internal.engineering_file_cards import _declarations

CASES = {
    "const unsigned x = 1;": "x",
    "const std::string s = 'a';": "s",
    "const moon::Foo bar;": "bar",
    "const double PI = 3.14;": "PI",
    "static const int kMax = 3;": "kMax",
    "constexpr int N = 4;": "N",
    "const char *name = 'x';": "name",
    "const std::vector<int> v;": "v",
}


def test_const_branch_returns_variable_name() -> None:
    for line, want in CASES.items():
        got = next(iter(_declarations([line])), None)
        assert got == want, f"{line!r} -> {got!r}, 期望 {want!r}"


def test_real_file_symbols_are_not_type_keywords() -> None:
    """回归实测文件：textutil.cpp 的旧输出全是类型关键字。"""

    source = "const unsigned x = 1;\nconst std::string s = 'a';\nint add(int a, int b) { return a + b; }\n"
    symbols = list(_declarations(source.splitlines()))
    assert "unsigned" not in symbols and "std" not in symbols
    assert {"x", "s", "add"} <= set(symbols)
