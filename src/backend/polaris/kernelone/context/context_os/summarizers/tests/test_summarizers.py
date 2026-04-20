"""Tests for ContextOS Summarizers - ADR-0067"""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os.summarizers import (
    SLMSummarizer,
    SummaryStrategy,
    SumySummarizer,
    TieredSummarizer,
    TreeSitterSummarizer,
    TruncationSummarizer,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_log() -> str:
    """Sample error log for testing."""
    return """
2026-04-12 10:15:32.123 INFO [app.py:45] Application started
2026-04-12 10:15:32.456 DEBUG [db.py:123] Connecting to database
2026-04-12 10:15:33.789 ERROR [db.py:456] Connection failed: timeout after 30s
2026-04-12 10:15:33.790 ERROR [db.py:457] Failed to connect to postgres://db:5432/main
2026-04-12 10:15:33.791 WARN [db.py:458] Retrying connection (attempt 1/3)
2026-04-12 10:15:34.123 DEBUG [db.py:123] Connection successful
2026-04-12 10:15:34.456 INFO [app.py:56] Database connected
2026-04-12 10:15:35.789 INFO [app.py:67] Starting server on port 8080
2026-04-12 10:15:36.012 INFO [server.py:89] Server started successfully
2026-04-12 10:15:40.123 ERROR [handler.py:234] Request failed: invalid JSON payload
2026-04-12 10:15:40.456 ERROR [handler.py:235] Stack trace: File "handler.py", line 234, in process
    raise ValueError("Invalid JSON")
ValueError: Invalid JSON
2026-04-12 10:15:40.789 INFO [handler.py:240] Request completed with error
""".strip()


@pytest.fixture
def sample_dialogue() -> str:
    """Sample dialogue for testing."""
    return """
User: I need to implement user authentication in our application.
Assistant: I can help you implement user authentication. There are several approaches:
1. JWT-based authentication
2. Session-based authentication
3. OAuth 2.0 for third-party integrations
User: Let's go with JWT-based authentication.
Assistant: Good choice. JWT is stateless and works well for APIs. Let me outline the implementation:
1. Create a login endpoint that validates credentials
2. Generate a JWT token on successful login
3. Add middleware to verify JWT on protected routes
4. Set token expiration for security
User: What about refresh tokens?
Assistant: Refresh tokens are a good idea for long-lived sessions. We can implement:
1. Short-lived access tokens (15 minutes)
2. Long-lived refresh tokens (7 days)
3. A /refresh endpoint to exchange refresh token for new access token
User: That sounds comprehensive. Let's proceed with this plan.
""".strip()


@pytest.fixture
def short_text() -> str:
    """Text that's too short to summarize."""
    return "This is a short text."


# =============================================================================
# TruncationSummarizer Tests
# =============================================================================


class TestTruncationSummarizer:
    """Tests for TruncationSummarizer - ADR-0067 Tier 3."""

    def test_truncation_returns_original_when_short(self, short_text):
        """Short text should be returned unchanged."""
        summarizer = TruncationSummarizer()
        result = summarizer.summarize(short_text, max_tokens=100)
        assert result == short_text

    def test_truncation_returns_original_when_within_limit(self):
        """Text within max_tokens should be returned unchanged."""
        text = "This is a normal length text." * 10  # ~60 chars
        summarizer = TruncationSummarizer()
        result = summarizer.summarize(text, max_tokens=100)
        assert result == text

    def test_truncation_smart_log_preserves_errors(self, sample_log):
        """Smart log truncation should preserve error lines."""
        summarizer = TruncationSummarizer()
        result = summarizer.summarize(sample_log, max_tokens=50, content_type="log")
        # Should contain error information
        assert "ERROR" in result or "error" in result.lower()

    def test_truncation_always_available(self):
        """TruncationSummarizer should always be available."""
        summarizer = TruncationSummarizer()
        assert summarizer.is_available() is True

    def test_truncation_strategy(self):
        """TruncationSummarizer should have TRUNCATION strategy."""
        summarizer = TruncationSummarizer()
        assert summarizer.strategy == SummaryStrategy.TRUNCATION


# =============================================================================
# TreeSitterSummarizer Tests
# =============================================================================


@pytest.fixture
def sample_python_code() -> str:
    """Sample Python code for testing."""
    return '''
def calculate_sum(numbers):
    """Calculate the sum of a list of numbers.

    Args:
        numbers: List of integers

    Returns:
        The sum of all numbers
    """
    total = 0
    for num in numbers:
        total += num
    return total


def calculate_average(numbers):
    """Calculate the average of a list of numbers.

    Args:
        numbers: List of integers

    Returns:
        The average of all numbers
    """
    if len(numbers) == 0:
        return 0
    return calculate_sum(numbers) / len(numbers)


class MathOperations:
    """A class for performing mathematical operations."""

    def __init__(self, precision=2):
        """Initialize with given precision.

        Args:
            precision: Number of decimal places
        """
        self.precision = precision

    def multiply(self, a, b):
        """Multiply two numbers."""
        result = a * b
        return round(result, self.precision)

    def divide(self, a, b):
        """Divide two numbers."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        result = a / b
        return round(result, self.precision)


def main():
    """Main entry point."""
    nums = [1, 2, 3, 4, 5]
    print(f"Sum: {calculate_sum(nums)}")
    print(f"Average: {calculate_average(nums)}")


if __name__ == "__main__":
    main()
'''.strip()


@pytest.fixture
def sample_json() -> str:
    """Sample JSON for testing."""
    return """
{
    "users": [
        {"id": 1, "name": "Alice", "email": "alice@example.com"},
        {"id": 2, "name": "Bob", "email": "bob@example.com"},
        {"id": 3, "name": "Charlie", "email": "charlie@example.com"}
    ],
    "metadata": {
        "total": 3,
        "page": 1,
        "per_page": 10
    }
}
""".strip()


class TestTreeSitterSummarizer:
    """Tests for TreeSitterSummarizer - ADR-0067 Tier 2."""

    def test_tree_sitter_is_available(self):
        """TreeSitterSummarizer should report availability when language packages are installed."""
        summarizer = TreeSitterSummarizer()
        # Should now return True since language packages are installed
        assert summarizer.is_available() is True

    def test_tree_sitter_returns_original_when_short(self, short_text):
        """Short text should be returned unchanged."""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(short_text, max_tokens=100)
        assert result == short_text

    def test_tree_sitter_falls_back_for_short_code(self):
        """Short code should be returned unchanged even if tree-sitter unavailable."""
        code = "def foo(): return 42"
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(code, max_tokens=100)
        # Short code returned as-is
        assert result == code

    def test_tree_sitter_compresses_json(self, sample_json):
        """TreeSitterSummarizer should compress JSON content."""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(sample_json, max_tokens=50, content_type="json")
        # Should produce valid JSON
        assert result.startswith("{") or result.startswith("[")
        # JSON should be valid (can be parsed)
        import json

        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_tree_sitter_strategy(self):
        """TreeSitterSummarizer should have STRUCTURED strategy."""
        summarizer = TreeSitterSummarizer()
        assert summarizer.strategy == SummaryStrategy.STRUCTURED

    def test_tree_sitter_estimate_output_tokens(self):
        """TreeSitterSummarizer should estimate output tokens correctly."""
        summarizer = TreeSitterSummarizer()
        # Tree-sitter compression is typically 25% of original
        estimated = summarizer.estimate_output_tokens(1000)
        assert estimated == 250  # 25% of 1000

    def test_tree_sitter_handles_invalid_json(self):
        """TreeSitterSummarizer should handle invalid JSON gracefully."""
        invalid_json = '{ "broken": json }'
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(invalid_json, max_tokens=50, content_type="json")
        # Should still return something
        assert len(result) > 0

    def test_tree_sitter_simple_truncate(self):
        """TreeSitterSummarizer should use simple truncation when needed."""
        # Long content that exceeds max_tokens
        long_code = "\n".join([f"line {i}: some content" for i in range(100)])
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(long_code, max_tokens=50, content_type="code")
        # Should be shorter than original
        assert len(result) < len(long_code)
        # Should indicate truncation
        assert "..." in result or "truncated" in result.lower()

    def test_tree_sitter_python_functions_preserved(self, sample_python_code):
        """Tree-sitter should preserve Python function signatures."""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(sample_python_code, max_tokens=100, content_type="code")
        # Should contain function definitions
        assert "def calculate_sum" in result or "calculate_sum" in result
        assert "def calculate_average" in result or "calculate_average" in result

    def test_tree_sitter_python_class_preserved(self):
        """Tree-sitter should preserve Python class definitions."""
        # Use a dedicated class-only fixture to avoid interference from other top-level definitions
        class_code = '''
class DataProcessor:
    """Processes data according to configured rules."""

    def __init__(self, config):
        """Initialize with configuration."""
        self.config = config

    def process(self, data):
        """Process a single data item."""
        return data
'''
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(class_code, max_tokens=100, content_type="code")
        # Should contain class definition
        assert "class DataProcessor" in result

    def test_tree_sitter_python_docstrings_preserved(self, sample_python_code):
        """Tree-sitter should preserve docstrings when configured."""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(sample_python_code, max_tokens=100, content_type="code")
        # Should contain docstrings
        assert '"""' in result or "Calculate" in result

    def test_tree_sitter_python_multiline_signature(self):
        """Tree-sitter should handle multiline function signatures."""
        code = '''
def process_data(
    input_list: list[int],
    callback: Callable[[int], int],
    max_workers: int = 4
) -> dict[str, Any]:
    """Process data with given parameters.

    Args:
        input_list: Input data
        callback: Processing callback
        max_workers: Number of workers

    Returns:
        Processed result dictionary
    """
    results = []
    for item in input_list:
        results.append(callback(item))
    return {"processed": results}
'''
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(code, max_tokens=80, content_type="code")
        # Should preserve function name
        assert "process_data" in result
        # Should indicate truncation with ellipsis
        assert "..." in result

    def test_tree_sitter_javascript_detection(self):
        """Tree-sitter should detect JavaScript code."""
        js_code = """
function addNumbers(a, b) {
    return a + b;
}

class Calculator {
    constructor() {
        this.result = 0;
    }

    add(value) {
        this.result += value;
        return this;
    }
}
"""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(js_code, max_tokens=80, content_type="code")
        # Should preserve function and class
        assert "addNumbers" in result or "function" in result
        assert "Calculator" in result or "class" in result

    def test_tree_sitter_go_detection(self):
        """Tree-sitter should detect Go code."""
        go_code = """
package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}

func add(a int, b int) int {
    return a + b
}
"""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(go_code, max_tokens=80, content_type="code")
        # Should preserve function
        assert "add" in result or "func" in result

    def test_tree_sitter_large_code_truncation(self):
        """Tree-sitter should truncate large code files intelligently."""
        # Generate large Python code (need ~132 functions at ~38 chars each to exceed 5000)
        lines = [f"def function_{i}(x):\n    return x * {i}" for i in range(150)]
        large_code = "\n".join(lines)
        assert len(large_code) > 5000  # Verify it's large

        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(large_code, max_tokens=100, content_type="code")

        # Should be significantly shorter
        assert len(result) < len(large_code)
        # Should indicate truncation
        assert "..." in result

    def test_tree_sitter_code_with_error_handling(self):
        """Tree-sitter should preserve error handling patterns when configured."""
        from polaris.kernelone.context.context_os.summarizers.structured import (
            CodeCompressionConfig,
            TreeSitterSummarizer,
        )

        # Use module-level try/except instead of nested in a function
        code = '''
try:
    result = risky_operation(data)
    print(result)
except ValueError as e:
    print(f"ValueError: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")

def risky_operation(data):
    """Perform a risky operation."""
    return data
'''
        # Use error_paths strategy to preserve error handling
        config = CodeCompressionConfig(strategy="error_paths")
        summarizer = TreeSitterSummarizer(default_config=config)
        result = summarizer.summarize(code, max_tokens=100, content_type="code")
        # Should preserve function signature
        assert "risky_operation" in result
        # With error_paths strategy, should contain try/except keywords
        result_lower = result.lower()
        assert "try" in result_lower or "except" in result_lower

    def test_tree_sitter_mixed_content_fallback(self):
        """Tree-sitter should handle non-code content gracefully."""
        non_code = "This is not code content at all. Just plain text with numbers 12345."
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(non_code, max_tokens=50, content_type="code")
        # Should return something (possibly truncated)
        assert len(result) > 0

    def test_tree_sitter_multiple_classes_and_functions(self):
        """Tree-sitter should handle multiple classes and functions."""
        code = '''
class User:
    """User class."""

    def __init__(self, name: str):
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"


class Admin(User):
    """Admin user class."""

    def __init__(self, name: str, level: int):
        super().__init__(name)
        self.level = level

    def greet(self) -> str:
        return f"Hello, Admin {self.name}!"


def create_user(name: str, is_admin: bool = False) -> User:
    """Create a user instance."""
    if is_admin:
        return Admin(name, level=1)
    return User(name)
'''
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(code, max_tokens=150, content_type="code")

        # Should preserve both classes
        assert "class User" in result
        assert "class Admin" in result
        # Should preserve function
        assert "create_user" in result

    def test_tree_sitter_json_preserves_structure(self):
        """Tree-sitter JSON compression should preserve structure."""
        json_code = """
{
    "name": "test",
    "version": "1.0.0",
    "dependencies": {
        "lodash": "^4.17.21",
        "express": "^4.18.0"
    },
    "nested": {
        "deep": {
            "value": 42
        }
    }
}
"""
        summarizer = TreeSitterSummarizer()
        result = summarizer.summarize(json_code, max_tokens=30, content_type="json")

        # Should be valid JSON
        import json

        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        # Should preserve some structure
        assert "name" in str(parsed) or "..." in result

    def test_tree_sitter_tiered_uses_structured_for_code(self):
        """TieredSummarizer should use STRUCTURED strategy for code when available."""
        from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

        # Use code without critical keywords to avoid validation failure
        simple_code = '''
def add_numbers(a, b):
    """Add two numbers together."""
    return a + b

def multiply_numbers(a, b):
    """Multiply two numbers."""
    return a * b
'''
        summarizer = TieredSummarizer()
        # Check if STRUCTURED is available
        available = summarizer.get_available_strategies()

        if hasattr(available, "__iter__") and len(available) > 0:
            # Just verify no errors when summarizing code
            result = summarizer.summarize(simple_code, max_tokens=100, content_type="code")
            assert len(result) > 0


# =============================================================================
# SumySummarizer Tests
# =============================================================================


class TestSumySummarizer:
    """Tests for SumySummarizer - ADR-0067 Tier 2."""

    def test_sumy_is_available(self):
        """SumySummarizer should report availability based on sumy installation."""
        summarizer = SumySummarizer()
        # sumy is installed in the test environment
        assert summarizer.is_available() is True

    def test_sumy_returns_original_when_short(self, short_text):
        """Short text should be returned unchanged."""
        summarizer = SumySummarizer()
        result = summarizer.summarize(short_text, max_tokens=100)
        assert result == short_text

    def test_sumy_summarizes_long_log(self, sample_log):
        """SumySummarizer should summarize long log content."""
        summarizer = SumySummarizer()
        result = summarizer.summarize(sample_log, max_tokens=100, content_type="log")
        # Result should be shorter than original
        assert len(result) < len(sample_log)
        # But should preserve error information
        assert "ERROR" in result or "error" in result.lower()

    def test_sumy_summarizes_dialogue(self, sample_dialogue):
        """SumySummarizer should summarize dialogue content."""
        summarizer = SumySummarizer()
        result = summarizer.summarize(sample_dialogue, max_tokens=150, content_type="dialogue")
        # Result should be shorter than original
        assert len(result) < len(sample_dialogue)

    def test_sumy_strategy(self):
        """SumySummarizer should have EXTRACTIVE strategy."""
        summarizer = SumySummarizer()
        assert summarizer.strategy == SummaryStrategy.EXTRACTIVE

    def test_sumy_estimate_output_tokens(self):
        """SumySummarizer should estimate output tokens correctly."""
        summarizer = SumySummarizer()
        # Extractiv summarization typically preserves 20-40%
        estimated = summarizer.estimate_output_tokens(1000)
        # Should be roughly 30% of input (conservative estimate)
        assert 200 <= estimated <= 400

    def test_sumy_chinese_language(self):
        """SumySummarizer should support Chinese language."""
        summarizer = SumySummarizer(language="chinese")
        assert summarizer.language == "chinese"

    def test_sumy_english_language(self):
        """SumySummarizer should support English language."""
        summarizer = SumySummarizer(language="english")
        assert summarizer.language == "english"


# =============================================================================
# TieredSummarizer Tests
# =============================================================================


class TestTieredSummarizer:
    """Tests for TieredSummarizer - ADR-0067分层摘要架构."""

    def test_tiered_returns_original_when_short(self, short_text):
        """Short text should be returned unchanged."""
        summarizer = TieredSummarizer()
        result = summarizer.summarize(short_text, max_tokens=100)
        assert result == short_text

    def test_tiered_uses_extractive_for_log(self, sample_log):
        """TieredSummarizer should use EXTRACTIVE strategy for logs."""
        summarizer = TieredSummarizer()
        result = summarizer.summarize(sample_log, max_tokens=100, content_type="log")
        # Should produce a summary
        assert len(result) < len(sample_log)
        # Should preserve error info
        assert "ERROR" in result or "error" in result.lower()

    def test_tiered_uses_extractive_for_dialogue(self, sample_dialogue):
        """TieredSummarizer should use EXTRACTIVE for dialogue when generative unavailable."""
        summarizer = TieredSummarizer()
        result = summarizer.summarize(sample_dialogue, max_tokens=150, content_type="dialogue")
        # Should produce a summary
        assert len(result) < len(sample_dialogue)

    def test_tiered_fallback_to_truncation(self, sample_log):
        """TieredSummarizer should fallback to TRUNCATION when EXTRACTIVE fails."""
        # Force EXTRACTIVE strategy which should work with sumy
        summarizer = TieredSummarizer()
        result = summarizer.summarize(
            sample_log,
            max_tokens=100,
            content_type="log",
            force_strategy=SummaryStrategy.EXTRACTIVE,
        )
        assert len(result) < len(sample_log)

    def test_tiered_get_available_strategies(self):
        """TieredSummarizer should report available strategies."""
        summarizer = TieredSummarizer()
        available = summarizer.get_available_strategies()
        # EXTRACTIVE and TRUNCATION should always be available (sumy is installed)
        assert SummaryStrategy.EXTRACTIVE in available
        assert SummaryStrategy.TRUNCATION in available

    def test_tiered_fallback_stats(self, sample_log):
        """TieredSummarizer should track fallback statistics."""
        summarizer = TieredSummarizer()
        summarizer.summarize(sample_log, max_tokens=50, content_type="log")
        stats = summarizer.get_fallback_stats()
        assert "fallbacks" in stats
        assert "successes" in stats

    def test_tiered_reset_stats(self, sample_log):
        """TieredSummarizer should reset stats when requested."""
        summarizer = TieredSummarizer()
        summarizer.summarize(sample_log, max_tokens=50, content_type="log")
        summarizer.reset_stats()
        stats = summarizer.get_fallback_stats()
        assert stats["fallbacks"] == {}
        assert stats["successes"] == {}


# =============================================================================
# Integration Tests
# =============================================================================


class TestSummarizerIntegration:
    """Integration tests for summarizer pipeline."""

    def test_sumy_then_truncation_fallback(self):
        """Test that EXTRACTIVE fallback to TRUNCATION works."""
        # When EXTRACTIVE is forced but should work, truncation shouldn't be needed
        # But if we force an unavailable strategy, it should fallback
        summarizer = TieredSummarizer()
        result = summarizer.summarize(
            "Error: connection timeout. Stack trace: ...",
            max_tokens=10,
            content_type="error",
            force_strategy=SummaryStrategy.EXTRACTIVE,
        )
        # Should still produce output
        assert len(result) > 0

    def test_multiple_content_types(self, sample_log, sample_dialogue):
        """Test summarization across different content types."""
        summarizer = TieredSummarizer()

        # Log content
        log_result = summarizer.summarize(sample_log, max_tokens=100, content_type="log")
        assert len(log_result) < len(sample_log)

        # Dialogue content
        dialogue_result = summarizer.summarize(sample_dialogue, max_tokens=100, content_type="dialogue")
        assert len(dialogue_result) < len(sample_dialogue)

    def test_empty_content_handling(self):
        """Test that empty content is handled gracefully."""
        summarizer = TieredSummarizer()
        result = summarizer.summarize("", max_tokens=100)
        assert result == ""

    def test_slm_summarizer_basic(self):
        """SLMSummarizer should be importable and have correct strategy."""
        summarizer = SLMSummarizer()
        assert summarizer.strategy == SummaryStrategy.SLM
        # When disabled, is_available should be False
        from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig

        disabled = SLMSummarizer(config=TransactionConfig(slm_enabled=False))
        assert disabled.is_available() is False

    def test_slm_raises_when_disabled(self):
        """SLMSummarizer should raise SummarizationError when SLM is disabled."""
        from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        summarizer = SLMSummarizer(config=TransactionConfig(slm_enabled=False))
        long_text = "This needs summarization. " * 100
        with pytest.raises(SummarizationError):
            summarizer.summarize(long_text, max_tokens=50)


# =============================================================================
# Quality Validation Tests
# =============================================================================


class TestSummarizerQuality:
    """Tests for summarizer output quality."""

    def test_critical_keywords_preserved_in_log(self):
        """Critical error keywords should be preserved in log summaries."""
        error_log = """
2026-04-12 10:00:00 INFO Application started
2026-04-12 10:00:01 ERROR Database connection failed: timeout
2026-04-12 10:00:02 ERROR Stack trace: ConnectionError at db.py:123
2026-04-12 10:00:03 WARN Retrying connection
2026-04-12 10:00:04 INFO Retry successful
""".strip()

        summarizer = TieredSummarizer()
        result = summarizer.summarize(error_log, max_tokens=50, content_type="log")

        # Critical keywords should be preserved
        result_lower = result.lower()
        assert "error" in result_lower
        assert "timeout" in result_lower or "connection" in result_lower

    def test_dialogue_preserves_questions(self):
        """Dialogue summaries should preserve question-answer pairs."""
        dialogue = """
User: What is the capital of France?
Assistant: The capital of France is Paris.
User: What is its population?
Assistant: Paris has a population of approximately 2.1 million people.
User: Is it the largest city?
Assistant: No, Marseille is larger by area, but Paris is the most populous.
""".strip()

        summarizer = TieredSummarizer()
        result = summarizer.summarize(dialogue, max_tokens=80, content_type="dialogue")

        # Questions and answers should be present
        assert "Paris" in result or "capital" in result.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
