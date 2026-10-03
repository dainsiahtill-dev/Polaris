# Execution budget must not depend on Bench observation tags

Status: candidate bugfix under existing master plan; not model/Bench qualification.

CodeGraph exposed a direct coupling: tasking adds a complexity bonus for
factory_bench tags and raises every observed task's output base to128000 and
input base to160000. These tags describe an internal test observer, not the
task's language, targets, scope, acceptance or model capabilities. This violates
the declared test/production boundary and prevents a representative benchmark.

Remove this observer-specific branch from the existing strategy owner. Preserve
ordinary task-type, phase, target/scope complexity and model-window policies,
all evidence requirements and all verifier/Bench thresholds. Large real tasks
can still receive the same large budget from their real profile. Never force
thinking off or silently change model binding as part of this fix.

Verification: identical profile/requirements with and without each observation
tag must produce identical strategy and contract controls; existing large-task
and repair tests remain green. This proves policy parity, not that a smaller
budget makes MiMo finish. Exact provider request and live outcome remain required.
