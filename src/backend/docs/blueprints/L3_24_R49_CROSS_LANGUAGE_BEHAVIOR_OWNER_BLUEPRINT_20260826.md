# L3-24 r49 cross-language behavior owner blueprint

## Exact-run failure

`factory_310ea6b5ac0a` built successfully and passed delivery-depth gates, then failed five Python `unittest` behavior checks. Every Director repair request called `edit_file`; the last request was nevertheless forced onto unrelated `src/moon_phase.cpp` and produced no effect.

## Proven cause

Python tests observed a compiled C++ CLI. Existing causal discovery resolved Python symbols only, so `MoonCipher::encrypt` could not bind to `src/cipher.cpp`. Before owner coverage was evaluated, harness fallback added every changed native source. Stable diagnostics then rotated across unrelated files.

## Invariant

Behavior observer paths never become mutation authority when runtime evidence proves native owners. A Python subprocess test may contribute:

1. an existing native CLI entrypoint;
2. a unique existing C/C++ `Type::method` definition.

Ambiguous definitions produce no owner. CE/JobToken scope remains authoritative.

## Verification

- TDD RED reproduced broad fanout.
- Focused regression GREEN.
- 162 related tests passed.
- Production Ruff, Mypy, and diff-check passed.
- Exact r49 validation replay narrowed targets to `app/main.cpp` and `src/cipher.cpp`.
- Fresh isolated r50 required for live completion evidence.
