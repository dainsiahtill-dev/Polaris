#!/usr/bin/env python3
import json

file_path = r'C:\Users\dains\Documents\GitLab\polaris\prompts\generic.json'

with open(file_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

pm_prompt = data['templates']['pm_prompt']

# 在Encoding guardrail之前插入新规则
old_section = 'Encoding guardrail (HARD RULE for any PowerShell you run):'
new_section = '''PROJECT TYPE DETECTION (CRITICAL):
- You MUST correctly identify the project type from GLOBAL REQUIREMENTS and CURRENT PLAN.
- Supported types: TypeScript, JavaScript, Python, Go, Rust.
- Look for explicit language indicators: "TypeScript", "Python", "Go", "Rust", "JavaScript", "Node.js", "Deno", etc.
- Look for file extensions mentioned: .ts, .js, .py, .go, .rs.
- Look for runtime indicators: "npm", "yarn", "pnpm", "pip", "cargo", "go mod".
- The overall_goal MUST reflect the correct project type (e.g., "Build TypeScript CLI app", NOT "Build Python api" for a TypeScript project).
- Task titles and goals MUST use the correct language (e.g., "Bootstrap TypeScript Project", NOT "Bootstrap Python Api").
- target_files MUST use correct extensions (e.g., .ts for TypeScript, .py for Python).
- If the project type is ambiguous, default to the most specific language mentioned in requirements.

TARGET FILES GENERATION (MANDATORY - THIS IS THE MOST IMPORTANT RULE):
- target_files MUST contain ONLY valid file paths with proper extensions.
- target_files MUST NEVER contain descriptions, sentences, or Chinese text like "见 `docs/product/adr.md".
- For NEW projects, you MUST generate the complete project structure with ALL necessary files.
- target_files should include:
  * Configuration files: package.json, tsconfig.json, pyproject.toml, go.mod, Cargo.toml
  * Source files: src/index.ts, src/main.py, main.go, cmd/server/main.go
  * Test files: src/index.test.ts, tests/test_main.py, main_test.go
  * Type definitions: src/types.ts, models.py, types.go
- Example for TypeScript CLI: ["package.json", "tsconfig.json", "src/index.ts", "src/cli.ts", "src/types.ts", "tests/cli.test.ts"]
- Example for Python: ["pyproject.toml", "src/main.py", "src/models.py", "tests/test_main.py"]
- Example for Go: ["go.mod", "main.go", "handlers.go", "storage.go", "main_test.go"]
- NEVER use placeholder text like "见 docs" or "参考文档" - always provide concrete file paths.
- If the file does not exist yet, include it in target_files so Director will create it.
- Each task should target 2-5 files maximum for atomic delivery.

TASK PHASE STRATEGY:
- First task should be "bootstrap": Create config files and project structure.
- Second task should be "core": Implement main logic and data models.
- Third task should be "features": Implement specific features from requirements.
- Fourth task should be "tests": Add comprehensive unit tests.
- Fifth task should be "polish": Add error handling, validation, documentation.
- DO NOT stop at bootstrap - continue until all requirements are implemented.

Encoding guardrail'''

pm_prompt = pm_prompt.replace(old_section, new_section)
data['templates']['pm_prompt'] = pm_prompt

with open(file_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print('Updated pm_prompt successfully')
