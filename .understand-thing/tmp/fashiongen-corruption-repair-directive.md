# FashionGenStudio Polaris repair directive

Repair the target workspace through the normal Polaris PM -> Chief Engineer -> Director -> QA chain.

Context:
- Workspace: `C:\Users\dains\Documents\GitLab\fashion-gen-studio`
- Director is configured globally in `C:\Users\dains\.polaris\config\llm\llm_config.json` as `codex_cli` with model `gpt-5.3-codex`.
- A previous Director run produced malformed text-patch output. The target source files were flattened/corrupted instead of receiving a safe patch.

Required repair:
1. Restore `src/backend/fashiongen_worker.py` to valid Python source code.
   - The current file is corrupted/flattened into one line.
   - Use `git show HEAD:src/backend/fashiongen_worker.py` as the formatting baseline if needed.
   - Reapply these intended wording repairs only:
     - Replace `or placeholder does not count` with `or mock fallback frame does not count`.
     - Replace `SVG placeholder` with `SVG template card`.
     - Replace `collage, placeholder, text labels` with `collage, template card, text labels`.
2. Restore `src/main/providers.ts` to valid TypeScript source code.
   - The current file is corrupted/flattened into one line.
   - Use `git show HEAD:src/main/providers.ts` as the formatting baseline if needed.
   - Reapply this intended wording repair only:
     - Replace `collage, placeholder, SVG` with `collage, template card, SVG`.
3. Remove the malformed Director protocol artifact directory if it exists:
   - `PATCH_FILE src/`
4. Verify:
   - `npm test` must pass.
   - `npm run build` must pass.
   - QA must no longer report `placeholder_content_detected`.

qa_rework_reason: placeholder_content_detected
Evidence:
- src/backend/fashiongen_worker.py:\bplaceholder\b
- src/main/providers.ts:\bplaceholder\b
- malformed artifact directory: PATCH_FILE src/
