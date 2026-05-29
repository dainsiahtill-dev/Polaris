Fix npm test failure in the FashionGenStudio target project.

Observed failure:
- Vitest file: tests/GenerationSpec.test.ts
- Failing assertion: compileProviderPrompt(spec) expected the prompt to contain Preserve garment truth.
- Current compiled prompt uses expanded garment fidelity wording such as Preserve the target garment product truth.

Task:
- Use the smallest target-project change that keeps the FashionGen prompt semantics intact.
- Align the prompt compiler or the test contract so npm test passes.
- Run npm test and npm run build.

Acceptance:
- npm test passes.
- npm run build passes.
- No Polaris repository business code is changed for this target repair.
