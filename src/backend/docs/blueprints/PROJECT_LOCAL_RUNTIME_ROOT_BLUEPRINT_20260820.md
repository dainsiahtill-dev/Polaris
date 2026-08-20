# Project-local runtime root blueprint

Status: Implementation active  
Date: 2026-08-20  
Encoding: UTF-8

## Problem

One workspace currently acquires three physical runtime locations:

```text
<workspace>/.polaris/**                persistent project metadata
<workspace>/runtime                    Launcher and Bench launch claim
~/.cache/kernelone/.polaris/projects/<workspace-key>/runtime
                                       actual storage resolver result
```

The split makes Instance Registry, backend process arguments, ContextOS readers,
lock authority, cleanup and Factory evidence disagree about one project's runtime
identity.

## Target architecture

```text
target project
└── .polaris
    ├── runtime        canonical hot runtime SSoT
    ├── history        durable project history
    └── storage_layout storage-layout audit metadata

explicit external runtime base (optional)
└── projects/<workspace-key>/runtime
```

`storage.layout` remains sole path authority. Default resolution returns
`<workspace>/.polaris/runtime`. An explicit external runtime root, cache root or
RAM-disk remains supported and workspace-key namespaced. No caller may invent
`<workspace>/runtime`.

## Responsibilities

- `storage.layout`: select canonical local runtime by default; preserve explicit
  external roots; discover legacy external roots read-only.
- Instance Supervisor/backend CLI: register and launch the resolved runtime root,
  never an instance-private or workspace-adjacent substitute.
- Factory Bench: claim the same project-local runtime in its isolated launch
  receipt. Bench remains internal test infrastructure.
- Readers: resolve local canonical root first, then legacy external namespaces for
  compatibility. Writers never dual-write.

## Data flow

```text
workspace
  -> storage.layout resolve
  -> canonical runtime identity
  -> InstanceRecord + backend environment + launch receipt
  -> KFS runtime writes/readers
```

## Assumptions and pre-mortem

- Local `.polaris/runtime` is already gitignored and belongs to target project.
- External roots are opt-in, not default fallback.
- Automatic copying is unsafe: an active runtime can be partially copied and
  create two authorities. Compatibility is read-only discovery only.
- Existing explicit `<workspace>/runtime` records must remain readable during
  restart, but new records must never emit that path.

## Verification

1. KernelOne and storage Cell tests prove local default, external opt-in, direct
   canonical-root recognition and legacy read-only fallback.
2. Instance tests prove default records use `<workspace>/.polaris/runtime`.
3. Factory Bench tests prove launch receipts and observed records use the same root.
4. Fresh isolated backend proves Instance Registry, process argument and
   `/v2/runtime/fingerprint` workspace binding remain consistent.

