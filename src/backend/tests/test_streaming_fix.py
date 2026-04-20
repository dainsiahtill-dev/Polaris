"""Quick verification tests for streaming latency fix"""

import pytest
from polaris.kernelone.llm.providers import BaseProvider
from polaris.infrastructure.llm.providers.minimax_provider import MiniMaxProvider


def test_minimax_has_true_streaming():
    """
    CRITICAL TEST: Verify MiniMax provider has true streaming implementation.
    
    Before fix: MiniMax used BaseProvider.invoke_stream (simulated)
    After fix: MiniMax has its own invoke_stream (true streaming)
    """
    provider = MiniMaxProvider()

    # Check that MiniMax overrides invoke_stream
    is_override = 'invoke_stream' in MiniMaxProvider.__dict__

    print(f"\nProvider class: {provider.__class__.__name__}")
    print(f"invoke_stream in class dict: {is_override}")
    print(f"Has attr (old check): {hasattr(provider, 'invoke_stream')}")

    assert is_override, "MiniMax MUST override invoke_stream for true streaming!"


def test_new_detection_logic():
    """
    Test the new streaming detection logic.
    
    Old logic: hasattr(provider, 'invoke_stream') - always True
    New logic: 'invoke_stream' in provider.__class__.__dict__ - True only if overridden
    """
    minimax = MiniMaxProvider()

    # New detection logic
    old_check = hasattr(minimax, 'invoke_stream')
    new_check = 'invoke_stream' in minimax.__class__.__dict__

    print(f"\nOld detection (hasattr): {old_check}")
    print(f"New detection (in __dict__): {new_check}")

    # Both should be True for MiniMax now
    assert old_check is True, "BaseProvider defines invoke_stream"
    assert new_check is True, "MiniMax now overrides invoke_stream"

    # The key difference: new_check distinguishes true vs simulated streaming
    class SimulatedProvider(BaseProvider):
        @classmethod
        def get_provider_info(cls):
            return None
        @classmethod
        def get_default_config(cls):
            return {}
        @classmethod
        def validate_config(cls, config):
            return None
        def health(self, config):
            return None
        def list_models(self, config):
            return []
        def invoke(self, prompt, model, config):
            return None

    simulated = SimulatedProvider()
    assert hasattr(simulated, 'invoke_stream') is True
    assert 'invoke_stream' not in simulated.__class__.__dict__
    assert new_check != ('invoke_stream' in simulated.__class__.__dict__), \
        "New check should work differently than old check"


def test_simulated_vs_true_streaming_detection():
    """
    Verify we can distinguish between providers with true vs simulated streaming.
    """
    # A provider that doesn't override invoke_stream (simulated)
    class SimulatedProvider(BaseProvider):
        @classmethod
        def get_provider_info(cls):
            pass
        @classmethod
        def get_default_config(cls):
            return {}
        @classmethod
        def validate_config(cls, config):
            pass
        def health(self, config):
            pass
        def list_models(self, config):
            pass
        def invoke(self, prompt, model, config):
            pass

    # A provider that overrides invoke_stream (true streaming)
    class TrueStreamingProvider(BaseProvider):
        @classmethod
        def get_provider_info(cls):
            pass
        @classmethod
        def get_default_config(cls):
            return {}
        @classmethod
        def validate_config(cls, config):
            pass
        def health(self, config):
            pass
        def list_models(self, config):
            pass
        def invoke(self, prompt, model, config):
            pass

        async def invoke_stream(self, prompt, model, config):
            yield "test"

    simulated = SimulatedProvider()
    true_streaming = TrueStreamingProvider()

    # Old detection: both True
    assert hasattr(simulated, 'invoke_stream') is True
    assert hasattr(true_streaming, 'invoke_stream') is True

    # New detection: only True for override
    assert 'invoke_stream' not in simulated.__class__.__dict__
    assert 'invoke_stream' in true_streaming.__class__.__dict__

    print("\n✅ New detection correctly distinguishes true vs simulated streaming!")


@pytest.mark.asyncio
async def test_invoke_stream_signature():
    """Verify MiniMax invoke_stream has correct signature and is async generator"""
    provider = MiniMaxProvider()

    # Check it's an async method
    import inspect
    assert inspect.isasyncgenfunction(provider.invoke_stream)

    # Check signature
    sig = inspect.signature(provider.invoke_stream)
    params = list(sig.parameters.keys())

    assert 'prompt' in params
    assert 'model' in params
    assert 'config' in params

    print(f"\n✅ invoke_stream signature: {sig}")


def test_provider_registry_integration():
    """Test that provider registry returns MiniMax with streaming"""
    from polaris.infrastructure.llm.providers.provider_registry import provider_manager

    provider = provider_manager.get_provider_instance('minimax')
    assert provider is not None

    is_override = 'invoke_stream' in provider.__class__.__dict__
    assert is_override, "Registry must return MiniMax with true streaming"

    print("\n✅ Provider registry integration: MiniMax has true streaming")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
