# Engineering bootstrap type boundary closure

Date: 2026-11-09. Baseline: `main6a75e53f7`.

## Problem and final decision

Engineering startup injected `ProviderAdapter` into `IProviderRegistryPort`. Container startup repeated the same error. `ProviderAdapter` implements response generation for `ServiceLocator`; it does not implement registry lookup, registration or configuration methods. Both startup paths now inject the existing infrastructure `provider_manager` into the registry port. Generation continues through the existing `ProviderAdapter` backed by that same manager.

`PermissionScope.command_cache` remains `Literal["disabled", "read_only", "read_write"]`. Python callers and persisted values can nevertheless supply invalid runtime objects. A local `object` annotation lets strict mypy analyze the existing exact-string/member validation without declaring it unreachable. Non-strings, string subclasses and unsupported values remain rejected. Permissions and serialization are unchanged.

Two additional bootstrap boundaries were verified while checking the complete assembly file. The embedding selection variable now has the existing `KernelEmbeddingPort` type, preserving its GPU/Ollama/Stub branches. The storage callback now returns the actual frozen K1 `StorageRoots` DTO: a nested adapter calls the existing Polaris resolver once and copies its 16 typed fields verbatim. No public conversion helper exists; K1 resolution already performed the same field projection after receiving the legacy callback result. The projection now satisfies the contract at registration.

## Architecture and responsibilities

```text
Engineering startup: _prepare_model_runtime
    |-- inject_kernelone_provider_runtime
    |       -> ServiceLocator [ProviderPort]
    |            -> ProviderAdapter.generate / generate_stream
    |                 -> existing provider_manager
    |
    `-- set_provider_manager_port(existing provider_manager)
                          ^
Container startup:       |
_inject_provider_manager_port
                          |
KernelOne registry consumers -> IProviderRegistryPort
                          -> existing provider_manager
                          -> lookup / registration / configuration

Host PermissionScope.command_cache [public Literal]
    -> object-typed local runtime observation
    -> exact str type + allowed-value validation
    -> unchanged saved permission scope

ensure_minimal_kernelone_bindings [existing assembly owner]
    -> register nested business_roots callback
         -> existing Cell public resolve_polaris_roots (one call)
         -> original Polaris roots (16 string fields)
         -> field-for-field K1 StorageRoots (frozen DTO)
         -> existing K1 storage resolution and policy checks

Cold public resolve_storage_roots_read_only
    -> existing canonical read-only resolution
    -> no writer callback invocation or absent-path creation
```

Bootstrap owns port registration. The infrastructure manager remains the provider registry owner; the generation adapter references it without creating another registry. `ServiceLocator` and `IProviderRegistryPort` retain their distinct responsibilities.

The storage Cell remains the source of Polaris path values. The nested adapter changes only the returned representation; K1 retains its existing path-policy, cache and resolution responsibilities. It recomputes no workspace key or path, coerces no field, adds no fallback and performs no additional resolver call. The original resolver's effects are unchanged. The separate public read-only path remains unchanged and was tested with an absent workspace.

The embedding annotation changes static inference only. No embedding selection, availability probe or fallback behavior changed. No `ProviderAdapter`, K1 protocol or storage Cell implementation was modified.

## Verified assumptions and failure risks

- The actual manager conforms to `IProviderRegistryPort`; strict structural checking and both injected runtime paths verify lookup and configuration behavior.
- Generation still uses `ProviderAdapter`, and its manager is the same object registered in the registry port.
- A typed `Literal` does not remove the need to validate arbitrary runtime values. Tests retain rejection of ten invalid values, including a string subclass, and acceptance of the three valid strings.
- The original storage resolver returns a separate slotted class, not the K1 frozen DTO. The adapter test verifies one original call, equality of every field, workspace/runtime/config paths, and immutability of all 16 fields.
- A mistaken registry fix could replace the generation port, create another manager or hide missing methods behind a cast. Tests assert both actual port identities and exercise registry methods.
- A mistaken storage fix could recompute roots, invoke the writer twice, introduce fallback or make a cold read create paths. Tests compare all fields and filesystem state, and the existing public storage-policy tests remain in the gate.

No whole-adapter cast, type-ignore, public contract weakening, new public facade, state owner or model-driven authority was added. Runtime cost of the storage adaptation is one DTO allocation and 16 field assignments.

## Verification and limitations

The original mypy run reproduced the cache-validation unreachable error and engineering registry-port incompatibility. Runtime regressions then failed independently for the engineering and container registry paths before their fixes. The storage regression failed on the original registered callback's return type before the DTO adaptation. A pristine-HEAD assembly shadow check recorded its storage callback error, two embedding assignment errors and registry-port error; the final assembly gate is clean.

Final results:

- Strict mypy passed the three changed production files and the new typed regression file.
- Ruff check and format checks passed all five changed Python files; `git diff --check` passed.
- Targeted bootstrap, cache-authority and public storage-boundary tests: **158 passed, 1 skipped**. The skipped case is Windows-specific (`test_storage_layout_cell.py:447`).
- The root task independently repeated the same test/static gates successfully. Independent source review found no changed-code defects; its additional narrow regressions passed 17 tests.

Tests exercised real in-process registry and storage boundaries. Configuration loading was isolated in the bootstrap unit test; no live provider request, model-health check, embedding request, service restart or generated-target edit occurred. These targeted results do not establish fresh native model acceptance or Windows execution. The root separately ran the full KernelOne release gate: its first run found two catalog-directory admission failures; after removing the nine historically retired catalog entries, all 12 stages passed, with 373 tests passed and 2 skipped. Both first-failure and passing reports remain under the external evidence directory (`root-kernelone-release-20261109.json` and `root-kernelone-release-20261109-r2.json`).

Evidence: `/home/dains/.polaris/bench_runs/qwen38-all120-20261105/dom-type-closure-20261109.json`.

Governance: [verification card](../../src/backend/docs/governance/templates/verification-cards/vc-20261109-engineering-bootstrap-type-boundaries.yaml). The card was added after implementation and verification; it records the observed baseline, decision and results retrospectively rather than claiming a pre-fix card existed.
