from __future__ import annotations

from polaris.delivery.cli.director.console_host import (
    RequestClarity,
    _assess_director_request_clarity,
)


def test_assess_director_request_clarity_handles_file_path_regex() -> None:
    clarity = _assess_director_request_clarity("请修改 polaris/delivery/cli/terminal_console.py 第120行")
    assert clarity in {
        RequestClarity.EXECUTABLE,
        RequestClarity.SEMI_CLEAR,
    }


def test_assess_director_request_clarity_marks_empty_as_vague() -> None:
    assert _assess_director_request_clarity("") == RequestClarity.VAGUE
