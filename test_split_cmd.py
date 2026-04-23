"""Test script to verify _split_command behavior."""
from polaris.cells.factory.verification_guard.internal.safe_executor import SafeExecutor

executor = SafeExecutor()

# Test case from failing test
# The test uses: 'python -c "raise RuntimeError(\"test error\")"'
# When Python parses this, it becomes: python -c "raise RuntimeError("test error")"
# Then our _split_command should produce: ['python', '-c', 'raise RuntimeError("test error")']

test_cmd = 'python -c "raise RuntimeError(\"test error\")"'
print(f"Input: {repr(test_cmd)}")
result = executor._split_command(test_cmd)
print(f"Split: {result}")

# Expected: ['python', '-c', 'raise RuntimeError("test error")']
expected = ['python', '-c', 'raise RuntimeError("test error")']
print(f"Expected: {expected}")
print(f"Match: {result == expected}")
