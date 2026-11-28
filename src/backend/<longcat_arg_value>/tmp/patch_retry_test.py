import pathlib

p = pathlib.Path("polaris/kernelone/llm/engine/tests/test_error_mapping_transient_5xx.py")
s = p.read_text(encoding="utf-8")
add = '''

def test_service_unavailable_is_retried_with_higher_budget() -> None:
    """5xx/529 抖动期常规 2 次尝试不够，会整轮 failed（实测 L1-03/L3-21 早期失败）。"""

    from polaris.kernelone.llm.engine.resilience import RetryConfig

    config = RetryConfig.from_options({})
    assert ErrorCategory.SERVICE_UNAVAILABLE in config.retryable_errors
    assert config.max_attempts_5xx >= 3
    assert config.max_attempts_5xx > config.max_attempts
'''
s = s.rstrip("\n") + "\n" + add
p.write_text(s, encoding="utf-8")
print("ok")
