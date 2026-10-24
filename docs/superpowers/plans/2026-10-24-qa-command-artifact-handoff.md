# QA Command Artifact Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Root owns integration on main; no branch/worktree.

**Goal:** Persist actual bounded command-produced files in the existing engineering journal and let current QA render the saved bytes without source writes or command replay.

**Architecture:** Reuse KernelOne storage/KFS and sandbox/process drain; add a host-only temporary output binding. Existing ToolObservation carries original artifacts, EngineeringRunService owns source/role authority, and the existing browser/image path renders host-verified journal data. No new controller, table, ledger or model-maintained contract.

**Tech Stack:** Existing Python3.12+, SQLite, KFS, Linux bubblewrap/process controls, installed Playwright/Chromium.

**Spec:** docs/superpowers/specs/2026-10-24-qa-command-artifact-handoff-design.md

## Global Constraints

- Direct main only; no target project changes/build/test/install. Synthetic fixtures only for module work, clearly labelled.
- Files/cache/audits under project.polaris, explicitUTF8; rtk commands; CodeGraph first; no service restart.
- Empty authority remains empty; unknown effects never replay; old observations/failed evidence remain.
- 16 captured files and1_000_000 decoded bytes aggregate; export limit is not hard disk quota.
- True source/mode/identity integrity and actual current candidate; no invented acceptance or benefit.

## Review Focus

- A host path outside the command-output namespace must never be mounted writable. Task1 real rejection/positive test.
- Artifact capture cannot quietly discard actual command failure or missing/oversized output. Task3 real effect tests.
- Re-rendering stale/foreign/tampered saved bytes cannot make current QA accept them. Task3 real Service test.
- Older JSON must decode and retain old payload shape; native messages/public projections must not expose artifact blobs. Task2 compatibility test.
- Cancellation/drain/commit failure and cold reopen cannot cause command replay or release a live writer. Task3/4 process+SQLite controls.

### Task1: KernelOne temporary command output resource

Files: modify polaris/kernelone/process/sandbox.py and public exports; create polaris/kernelone/process/output_capture.py; add polaris/kernelone/tests/test_command_output_capture.py. AgentA exclusive bucket.

Interfaces: `CapturedOutput(name: str,data: bytes)`; `open_command_output_capture(workspace: Path)` context manager returns `CommandOutputCapture.root: Path` and `.read(names: Sequence[str], *, max_files=16,max_bytes=1_000_000)->tuple[CapturedOutput,...]`. Uses current `kernelone.storage` runtime resolver, KFS allocation/read/cleanup. `build_workspace_sandbox_argv(...,capture_output_root: Path|None=None)` validates canonical dedicated runtime/command-output/32hex direct child, directory identity/symlinks/emptiness; original scope/control-root rules stay. No shell wrapper/pipe parsing or unsandboxed fallback.

- [ ] Write real readonly-source test: command writes output.svg under /sandbox-output, attempts source write and fails; after actual process drain KFS reads exact bytes and cleanup removes only own directory. Observe expectedRED before implementation.
- [ ] Cover no-capture oldargv, foreign/root/symlink/dirty path rejection, escaping/linked/special/missing/oversized files, real timeout/cancel drain before cleanup, and supported local storage root.
- [ ] Implement minimal public capability; run focused real K1 tests, Ruff/type check. Do not commit or touch other buckets; report RED/GREEN commands and actual effects.

### Task2: Artifact value and existing journal codec/projection

Files: public/contracts/engineering_run.py, internal/engineering_session.py, current engineering projection codec if needed, cell public exports; tests/test_engineering_artifact_persistence.py. AgentB exclusive bucket, no Service/loop/tools/browser edits.

Interface: frozen `ArtifactObservation(artifact_ref: str,name: str,media_type: str,data_base64: str,sha256: str,source_digest: str|None=None)`, validates bounded decoded bytes/hash/relative name/ref/source digest. Add default empty `ToolObservation.artifacts`; payload omits empty artifacts exactly as old images. Decode from actual journal in current path; no new table. `EngineeringSessionStore.artifact(session_id: str,artifact_ref: str)->ArtifactObservation|None` only finds committed observations for thatrun; original producing call identity is available to consumer from same journal. If returning producer identity separately is necessary choose a small query result and inform Root before changing interface.

- [ ] Write RED: record tool observation with artifact, cold reopen returns exact bytes/digest/name/ref without process execution; older nonartifact payload shape unchanged.
- [ ] Test corrupted/oversize/empty bytes, forged ref, foreign session, projection raw data removed, renderer not involved. Implement codec/value/query and run related session/image/cold tests.
- [ ] Return concrete source pin/changes/tests; leave artifacts private in model/public projections, never normalize unknown data to success.

### Task3: Real producer and saved-byte browser integration (Root)

Files: internal/engineering_artifacts.py (small adapter), engineering_tools.py, engineering_roles.py, engineering_loop.py, engineering_browser.py, public/engineering_service.py; generic tests/test_engineering_artifact_handoff.py and browser counterpart. Consumes Task1/2 APIs.

- [ ] Real RED through EngineeringRunService/scripted provider: readonly QA executes command with artifacts=['report.svg']; returned ref consumed by browser_observe(artifact_ref=...), actual pixels delivered in later provider request and same QA frame. Current version cannot pass.
- [ ] Capture adapter allocates unique host output, invokes existing EngineeringTools._process with optional binding, reads after known physical drain and cleans finally. Return actual command result plus ArtifactObservation; preserve command error vs capture error vs unknown. Host input binding callback uses existing verifier manifest; compare before/after, compute original stable source digest and never refresh it on render.
- [ ] Extend natural aliases/schemas once; only requested capture allocates resources. Existing no-capture calls and explicit empty scopes unchanged. Root passes configured capture adapter from existing service/loop, not new controller.
- [ ] Browser artifact_ref resolution uses existing store, original bytes/hash/run/workspace, current host authorization and current input digest. Feed saved bytes to trusted browser, not a same-name workspace decoy; original workspace/.polaris path guard remains. Preserve current QA proof and physical image transport.
- [ ] Real cases: captured SVG and PNG/HTML, missing/oversized captures after real exit, readonly source, normal aliases, explicit empty permissions, forged/crossrun/stale/revoked refs, decoy path, crash/cancel/UNKNOWN no replay; current and cold saved-byte captures. Source/test/browser semantics remain distinct.

### Task4: Qualification, integration and fresh original case

Files: current Cell/KernelOne graph/README assets only as actual changed interfaces require; this spec/plan and evidence. Root owns all commits.

- [ ] Run final focused+adjacent tests and source style/types. Run backend required architecture and KernelOne ALL gates, catalog baseline/delta; identify pre-existing failures honestly. Independent final code/effect review.
- [ ] One finite same-model/budget bad/healthy actual-artifact QA experiment if integrated image path ready. Scripted model proves transport, not semantic judgment. Any unknown cost stays unknown; no extra samples to force conclusion.
- [ ] Commit actual coherent migration on main. Freeze code/config/model and start originalL2-11 serial isolated through formal入口; Root personally assess its artifact quality after terminal and resource closeout. Do not rerun L110 indefinitely or count local experiments as native delivery.
