# Entropy Reduction Matrix (ERM) - Adaptive LLM Output Parsing

**Date**: 2026-04-04
**Status**: Blueprint
**Author**: Python Architecture Committee
**Deprecation Notice**: This blueprint supersedes basic parsing in `polaris/cells/roles/kernel/internal/pydantic_output_parser.py`

---

## 1. Problem Statement

### 1.1 Current Pain Points

| Issue | Location | Symptom |
|-------|----------|---------|
| Fragile JSON extraction | `pydantic_output_parser.py:155-201` | Regex fails on slight format variations |
| Blind retry | `instructor_client.py:149-173` | Re-calls LLM without actionable error context |
| No error analysis | Both | ValidationError.details not parsed into natural language |
| Single fallback | `pydantic_output_parser.py:203-273` | Falls back to GenericRoleResponse, no type coercion |
| Sync blocking | `instructor_client.py:281-327` | `_call_llm` uses `asyncio.to_thread` instead of native async |
| No cancellation | Both | Long retry loops cannot be cancelled |

### 1.2 Root Causes

1. **Extraction rigidity**: Regex patterns like `r"```json\s*(\{.*?\})\s*```"` fail on:
   - Trailing whitespace inside braces
   - Escaped characters within JSON strings
   - Natural language prefixes ("Here's the JSON:", "The result is:")

2. **Error opacity**: Pydantic `ValidationError` contains structured `line_errors` but code treats it as opaque string

3. **No progressive fallback**: Code either succeeds fully or jumps to GenericRoleResponse

---

## 2. Architecture Blueprint

### 2.1 State Machine: RobustParser States

```
┌─────────────────────────────────────────────────────────────────┐
│                    ENTROPY REDUCTION MATRIX                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐ │
│  │  RAW     │───▶│  CLEAN   │───▶│ EXTRACT  │───▶│ VALIDATE  │ │
│  │  INPUT   │    │  PHASE   │    │  PHASE   │    │   PHASE   │ │
│  └──────────┘    └──────────┘    └──────────┘    └───────────┘ │
│       │               │               │               │          │
│       │               │               │               ▼          │
│       │               │               │         ┌───────────┐    │
│       │               │               │         │ CORRECT  │    │
│       │               │               │         │  PHASE   │    │
│       │               │               │         └───────────┘    │
│       │               │               │               │          │
│       │               ▼               ▼               ▼          │
│       │          ┌──────────┐   ┌──────────┐   ┌───────────┐    │
│       │          │  SAFE    │   │ FALLBACK │   │  EXHAUST  │    │
│       │          │  NULL    │◀──│  CHAIN   │◀──│   ED      │    │
│       │          └──────────┘   └──────────┘   └───────────┘    │
│       │                                                     │    │
│       └─────────────────────────────────────────────────────┘    │
│                         (All paths lead to terminal state)      │
└─────────────────────────────────────────────────────────────────┘
```

**States:**
- `RAW_INPUT`: Initial string from LLM
- `CLEAN_PHASE`: Heuristic pre-processing (remove NL prefixes/suffixes)
- `EXTRACT_PHASE`: JSON extraction from various formats
- `VALIDATE_PHASE`: Pydantic schema validation
- `CORRECT_PHASE`: Auto-healing retry with detailed error feedback
- `FALLBACK_CHAIN`: Progressive type coercion fallback
- `SAFE_NULL`: Return safe NullObject to prevent cascade
- `EXHAUSTED`: Max retries reached, return best effort

### 2.2 Heuristic Pre-processing Rules

```python
CLEAN_RULES = [
    # Rule 1: Strip natural language prefixes
    (r"^(?:here(?:'s| is) (?:the )?(?:json|result|output|data|answer):?\s*)", ""),
    # Rule 2: Strip trailing explanations after JSON
    (r"(?:\n(?:that's?|here's?|this is|as requested).*)?$", ""),
    # Rule 3: Normalize whitespace in code blocks
    (r"```\s*json\s*", "```json "),
    # Rule 4: Remove invisible Unicode
    (r"[\u200b-\u200f\ufeff]", ""),
]
```

### 2.3 ValidationError → Correction Prompt Transformation

```python
def _build_correction_prompt(ve: ValidationError, schema: type[BaseModel]) -> str:
    """Transform Pydantic ValidationError into natural language correction prompt."""
    lines = ["Your previous output had the following issues:"]

    for err in ve.errors():
        loc = ".".join(str(l) for l in err["loc"])
        msg = err["msg"]
        inp = str(err["input"])[:50]  # Truncate long inputs

        if err["type"] == "missing":
            lines.append(f"- Missing required field '{loc}'")
        elif err["type"] == "string_type":
            lines.append(f"- Field '{loc}' must be {msg}, got: {inp}")
        elif err["type"] == "json_invalid":
            lines.append(f"- Invalid JSON at '{loc}': {msg}")
        else:
            lines.append(f"- Field '{loc}': {msg} (got: {inp})")

    lines.append(f"\nRequired schema: {schema.model_json_schema()[:500]}...")
    lines.append("\nOutput ONLY valid JSON matching the schema.")
    return "\n".join(lines)
```

### 2.4 Fallback Chain (Type Coercion)

```
Attempt 1: Parse with exact schema (T)
Attempt 2: Parse with schema + correction prompt
Attempt 3: Relax strict types (int → str, float → str)
Attempt 4: Extract partial data (only present fields)
Attempt 5: Return SafeNull[T] with raw_content preserved
```

---

## 3. Implementation Location

**New Module**: `polaris/kernelone/llm/robust_parser/`

```
polaris/kernelone/llm/robust_parser/
├── __init__.py
├── core.py          # RobustParser[T] generic class
├── states.py        # ParserState enum and transitions
├── cleaners.py      # Heuristic pre-processing rules
├── extractors.py     # Multi-pattern JSON extraction
├── correctors.py    # ValidationError → correction prompt
├── fallbacks.py     # Type coercion and SafeNull
└── contracts.py     # Public contracts
```

**Integration Points**:
- Replace usage of `PydanticOutputParser` in `polaris/cells/roles/kernel/internal/services/llm_invoker.py`
- Use `RobustParser` in `polaris/infrastructure/llm/instructor_client.py` for fallback path

---

## 4. API Design

### 4.1 Core Interface

```python
from polaris.kernelone.llm.robust_parser import RobustParser, ParseResult

class RobustParser(Generic[T]):
    """Adaptive parser with multi-stage fallback chain."""

    async def parse(
        self,
        response: str,
        *,
        schema: type[T],
        max_correction_turns: int = 3,
        llm_corrector: Callable[[str], Awaitable[str]] | None = None,
    ) -> ParseResult[T]:
        """Parse LLM response with auto-healing retry.

        Args:
            response: Raw LLM output string
            schema: Target Pydantic model type
            max_correction_turns: Max retry attempts for validation failures
            llm_corrector: Optional async callable to re-call LLM with correction prompt

        Returns:
            ParseResult with parsed data or safe fallback
        """
```

### 4.2 ParseResult Dataclass

```python
@dataclass(frozen=True)
class ParseResult(Generic[T]):
    """Result of RobustParser.parse()."""
    success: bool
    data: T | None
    raw_content: str
    state: ParserState
    correction_attempts: int
    error: str | None
    safe_null: bool  # True if returned SafeNull

    @property
    def is_safe_fallback(self) -> bool:
        return self.safe_null or self.state == ParserState.SAFE_NULL
```

---

## 5. Quality Gates

### 5.1 Unit Tests Required

| Module | Test File | Coverage |
|--------|-----------|----------|
| cleaners | `test_cleaners.py` | 100% rule coverage |
| extractors | `test_extractors.py` | JSON variant coverage |
| correctors | `test_correctors.py` | Error type → prompt mapping |
| fallbacks | `test_fallbacks.py` | SafeNull behavior |
| core | `test_robust_parser.py` | State machine transitions |

### 5.2 Integration Tests

- `test_parse_with_llm_corrector`: End-to-end with mock LLM
- `test_cancellation`: Verify async cancellation works
- `test_no_cascade`: SafeNull does not propagate errors

---

## 6. Deprecation Plan

| Old Component | New Replacement | Sunset Date |
|--------------|-----------------|-------------|
| `PydanticOutputParser.parse()` | `RobustParser.parse()` | 2026-05-01 |
| `StructuredLLMClient._create_with_fallback()` | Use `RobustParser` internally | 2026-05-01 |

---

## 7. Open Questions

1. **Cancellation**: Should `RobustParser` support `asyncio.CancellationError` propagation or suppress it?
2. **SafeNull**: Should `SafeNull[T]` implement `T` interface partially (returns None for unknown fields)?
3. **Logging**: Should each state transition emit a structured log event?

---

## 8. Implementation Phases

| Phase | Scope | Deliverable |
|-------|-------|-------------|
| 1 | Core state machine + Cleaners | `robust_parser/core.py`, `cleaners.py` |
| 2 | Extractors + Correctors | `extractors.py`, `correctors.py` |
| 3 | Fallbacks + SafeNull | `fallbacks.py` |
| 4 | Integration + Tests | Replace old parsers, add tests |
| 5 | Documentation + Deprecation | Update docs, mark old code |
