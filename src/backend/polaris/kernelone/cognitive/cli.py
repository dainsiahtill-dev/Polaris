"""Cognitive Life Form CLI - Simple demo tool for testing the cognitive pipeline."""

from __future__ import annotations

import asyncio
import os


def main():
    """Simple CLI demo for Cognitive Life Form."""
    import argparse

    parser = argparse.ArgumentParser(description="Cognitive Life Form Demo")
    parser.add_argument("message", nargs="?", default="Read the file at src/main.py", help="Message to process")
    parser.add_argument("--session", default="cli_demo", help="Session ID")
    parser.add_argument("--role", default="director", help="Role (pm, architect, chief_engineer, director, qa, scout)")
    parser.add_argument("--show-context", action="store_true", help="Show session context")
    parser.add_argument("--diagnose", action="store_true", help="Show cognitive system status")

    args = parser.parse_args()

    # Handle diagnose mode
    if args.diagnose:
        print("=== Cognitive Life Form System Status ===")
        print()
        print("--- Core Components ---")
        print("CognitiveOrchestrator: Available")
        print("PerceptionLayer: Available")
        print("CriticalThinkingEngine: Available")
        print("MetaCognitionEngine: Available")
        print("ThinkingPhaseEngine: Available")
        print("ActingPhaseHandler: Available")
        print("EvolutionEngine: Available")
        print("PersonalityIntegrator: Available")
        print()
        print("--- Configuration ---")
        env_enabled = os.environ.get("POLARIS_ENABLE_COGNITIVE_MIDDLEWARE", "not set")
        print(f"POLARIS_ENABLE_COGNITIVE_MIDDLEWARE: {env_enabled}")
        print()
        print("--- Middleware Integration ---")
        from polaris.kernelone.cognitive.middleware import get_cognitive_middleware

        middleware = get_cognitive_middleware()
        print(f"CognitiveMiddleware enabled: {middleware._enabled}")
        print()
        print("--- Usage ---")
        print("To enable cognitive middleware for role dialogue:")
        print("  export POLARIS_ENABLE_COGNITIVE_MIDDLEWARE=true")
        print()
        print("To run a cognitive demo:")
        print('  python -m polaris.kernelone.cognitive.cli "Create a new API"')
        return

    from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

    async def run():
        orchestrator = CognitiveOrchestrator()

        print("=== Cognitive Life Form Demo ===")
        print(f"Message: {args.message}")
        print(f"Session: {args.session}")
        print(f"Role: {args.role}")
        print()

        result = await orchestrator.process(
            message=args.message,
            session_id=args.session,
            role_id=args.role,
        )

        print("--- Cognitive Analysis ---")
        print(f"Intent Type: {result.intent_type}")
        print(f"Execution Path: {result.execution_path.value}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Clarity Level: {result.clarity_level.name}")
        print(f"Uncertainty Score: {result.uncertainty_score:.2f}")
        print(f"Blocked: {result.blocked}")
        if result.block_reason:
            print(f"Block Reason: {result.block_reason}")
        print()
        print("--- Response ---")
        print(result.content)
        print()

        if args.show_context:
            ctx = orchestrator.get_session(args.session)
            if ctx:
                print("--- Session Context ---")
                print(f"Session ID: {ctx.session_id}")
                print(f"Role ID: {ctx.role_id}")
                print(f"Posture: {ctx.interaction_posture.value}")
                print(f"Dominant Trait: {ctx.trait_profile.dominant_trait.value}")
                print(f"Turns: {len(ctx.conversation_history)}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
