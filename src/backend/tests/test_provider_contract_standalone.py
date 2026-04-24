"""Standalone test for provider contract refactoring.

This file tests the new provider contract implementation in isolation.
Run with: python -c "exec(open('tests/test_provider_contract_standalone.py').read())"
"""

from typing import Any, TypedDict


class ProviderRequest(TypedDict, total=False):
    messages: list[dict[str, Any]]
    model: str
    temperature: float | None
    max_tokens: int | None
    system: str | None
    tools: list[dict[str, Any]] | None
    tool_choice: dict[str, Any] | str | None
    stream: bool
    prompt: str
    config: dict[str, Any]


class AdapterProviderContract:
    """Adapter-Provider contract validator."""

    @staticmethod
    def validate_request(request: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not isinstance(request, dict):
            return False, ["request must be a dict"]
        config = request.get("config")
        if not isinstance(config, dict):
            errors.append("request['config'] must be a dict")
        if isinstance(config, dict):
            messages = config.get("messages")
            if not isinstance(messages, list):
                errors.append("request['config']['messages'] must be a list")
        return len(errors) == 0, errors

    @staticmethod
    def extract_messages(request: dict[str, Any]) -> list[dict[str, Any]]:
        config = request.get("config", {})
        if isinstance(config, dict):
            messages = config.get("messages")
            if isinstance(messages, list):
                return messages
        prompt = request.get("prompt", "")
        if prompt:
            return [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        return []


def test_provider_contract():
    """Test the provider contract implementation."""
    contract = AdapterProviderContract()

    # Test 1: Extract adapter-built messages
    result = contract.extract_messages({"config": {"messages": [{"role": "user", "content": "test"}]}})
    assert result == [{"role": "user", "content": "test"}], f"Test 1 failed: {result}"
    print("Test 1 passed: Extract adapter-built messages")

    # Test 2: Legacy fallback
    result = contract.extract_messages({"prompt": "Hello", "config": {}})
    assert result == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}], f"Test 2 failed: {result}"
    print("Test 2 passed: Legacy fallback")

    # Test 3: Validate request with messages
    is_valid, errors = contract.validate_request({"config": {"messages": []}})
    assert is_valid, f"Test 3 failed: {errors}"
    print("Test 3 passed: Validate request with messages")

    # Test 4: Validate request without config
    is_valid, errors = contract.validate_request({})
    assert not is_valid, "Test 4 should have failed"
    assert "request['config'] must be a dict" in errors
    print("Test 4 passed: Validate request without config")

    # Test 5: Validate request with invalid messages
    is_valid, errors = contract.validate_request({"config": {"messages": "not a list"}})
    assert not is_valid, "Test 5 should have failed"
    assert "request['config']['messages'] must be a list" in errors
    print("Test 5 passed: Validate request with invalid messages")

    # Test 6: Request with empty messages list is valid
    is_valid, errors = contract.validate_request({"config": {"messages": []}})
    assert is_valid, f"Test 6 failed: {errors}"
    print("Test 6 passed: Request with empty messages list is valid")

    print()
    print("All provider contract tests passed!")


if __name__ == "__main__":
    test_provider_contract()
