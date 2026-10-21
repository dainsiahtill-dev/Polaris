# Pelican SVG internal benchmark implementation

Work directly on main, no branches/worktrees. Temporary inputs, code for external judging, caches and evidence live under ~/.polaris/. Spec: ../specs/2026-10-21-pelican-role-benchmark-design.md.

1. Add a separate one-case catalog and fixtures for plainSVG requirements, same host/checks, external visual rubric. Preserve original120catalog bytes. No runtime/controller/tool/schema changes, targetartwork, document/loc gates or referenceimage generation.
2. Qualify exact fixture command with explicit synthetic mechanicalcounterexamples, and validate nativeLaunchInputs for bothsupported workflows withoutcreatingtarget/LLMcalls. Assess unchangedruntimebyte applicability ratherthan rerun unrelatedgates.
3. Qualify externalreadonlybrowserrenderer with genericrect/blank/invalid examples. Record actualbrowser/source hashes, no network or targetsourcedits. Renderer gives mechanics, root gives scenejudgment.
4. Independently reviewcase/fixtures/protocol andentryassumptions. Commit data/docs together, freeze source/model/host/rubric/renderer/seed andall10allocations.
5. Sequentiallystartallocatedfreshnativeinstances viaexistingowner. Observeactualfinalproviderrequests, roles, tools, currentchecks/QA andoriginalresourceclosure. Rootpersonallygradesactualrenderbefore next; preserveallfailures/unknowncosts.
6. Report rawSVG/PNG, ratings,actualchains, allattempt denominators, time/resources/unknownbills, label-hidden comparisons andlimitedscope. Original120coverage staysseparate; no platformcompletionclaim fromthisprobe.

Stops: unknown effects/resources, realsource/configdrift, or10allocatedattempts. A cleanlyclosed ordinaryartifactfailure remainsa recordedresult; no evaluatorfix/retry or expandinggates. Existingoldroleq1completednativeaccepted/rootqualityFAIL, other9NOTRUN afterusersteering; recordsunchanged.
