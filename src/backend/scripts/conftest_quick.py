"""Pytest configuration for new tests."""

# Disable asyncio mode warning
pytest_plugins = []


def pytest_configure(config):
    """Configure pytest."""
    config.option.asyncio_mode = "auto"
