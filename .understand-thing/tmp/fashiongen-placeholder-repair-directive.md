Fix the full-project QA blocker in FashionGenStudio.

Observed QA failure:
- qa_rework_reason: placeholder_content_detected
- Evidence:
  - src/backend/fashiongen_worker.py:\bplaceholder\b
  - src/main/providers.ts:\bplaceholder\b

Task:
- Use Polaris PM -> Chief Engineer -> Director -> QA.
- Repair only the concrete target project files needed to remove unfinished placeholder/stub markers from production source.
- Preserve existing provider behavior, public API shapes, and tests.
- Do not add TODO, FIXME, NotImplemented, placeholder, or stub markers.
- Run npm test and npm run build.

Acceptance:
- Full project QA no longer reports placeholder_content_detected for the two evidence files.
- npm test passes.
- npm run build passes.
