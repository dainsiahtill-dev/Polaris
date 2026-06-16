# Factory Bench Rigid Verdict Gate Blueprint (2026-06-16)

## Status

- Type: evaluation-platform hardening.
- Scope: `scripts/factory_bench/run_factory_bench.py` and
  `polaris/kernelone/benchmark/factory_audit.py`.
- Goal: make factory-bench a rigid measurement tool. A project may not be
  reported as `PASS` when the Polaris chain failed, QA was skipped/failed, a
  required governance artifact is missing, or wrong-product contamination is
  detected.

## Evidence

Live L2-11 evidence showed:

```text
[factory-bench] L2-11 PASS: chain=fail ... qa_ran=False qa_passed=False ... chain_exit=1
```

The generated artifact checks found a runnable-looking web shape and content
tokens, but the actual role chain failed and integration QA never ran. That is
not a weak-model capability issue; it is an evaluation verdict issue.

A sequential full L2 baseline after the fix produced `5/6` instead of hiding the
remaining defect. `L2-08` had enough generated files and passed static checks,
but the full-chain gates failed closed:

```text
L2-08 FAIL: chain=partial ... qa_ran=True qa_passed=False ... chain_exit=5
gate:chain_clean: FAIL
gate:integration_qa_passed: FAIL
```

That score drop is the intended behavior. The benchmark is now reporting the
chain truth instead of treating visible artifacts as a completed product.

Further L2-08 forensics showed the concrete QA failure was not opaque model
quality: `package.json` declared `scripts.test = "node test/check.js"` but no
`test/check.js` file existed. The rigid gate already failed it through QA; the
static audit layer now also reports this as a deterministic artifact defect:

```text
package_scripts: FAIL
script 'test' references missing local entrypoint: test/check.js
```

## Root Cause

`run_factory_bench.py` built `record["all_checks_passed"]` from
`build_factory_audit_record()`, which only evaluates deterministic artifact
checks. The runner then appended chain/QA/wrong-product facts to the record but
never folded them back into the final pass/fail bit used by logs and aggregate
counts.

## Design

Add a runner-local full-chain gate layer:

```text
project static checks
      |
      v
static_checks_passed
      |
      +--> plan artifact present
      +--> blueprint artifact present
      +--> QA verdict artifact present
      +--> chain_state == clean AND exit_code == 0
      +--> QA ran AND QA passed
      +--> wrong-product guard clear
      |
      v
all_checks_passed
```

The static checks remain visible and unchanged. The aggregate score now measures
the conjunction of product checks and platform chain correctness.

For generated projects that include `package.json`, the static artifact checks
also validate explicit local script entrypoints. The check is generic: it only
fails when a package script invokes a local file through an interpreter such as
`node`, `python`, or `sh`, and that file is absent. It does not require every
project to have tests and does not inspect domain-specific behavior.

## Non-Goals

- Do not improve any generated product.
- Do not add project-specific pass rules.
- Do not add project-specific pass rules or target-project business logic to
  `factory_audit.py`; static primitives must remain reusable artifact checks.
- Do not hide weaker scores. Score drops from this gate are intended evidence.

## Verification

- Unit tests prove static-pass + chain-fail becomes `FAIL`.
- Unit tests prove missing QA verdict and wrong-product suspect fail closed.
- Unit tests prove clean chain + QA pass preserves static pass.
- Unit tests prove missing local package script entrypoints fail static audit.
- Standard ruff/mypy/pytest gates apply.
