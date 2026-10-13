# Failed effect closure and independent QA

Delivery acceptance and resource cleanup remain separate. Missing or failed
required modalities, failed gates, tool lifecycle failures and TaskBoundary
failures remain in the Run Ledger projection after operational closure.

The generic TaskRuntime `QueryExecutionEffectClosureV1` observes the exact
workspace, task, external task, session, attempt, role, worker and run identity.
The current session must be terminal and nonresumable. Its parent close proof
must match the strict persisted registry reduction; every sealed operation must
be closed by its parent or aborted before execution. Pending, unknown,
dead-letter, corrupt, foreign or uncommitted mutations do not produce closure.
The query rereads session, registry and operation sources and grants no execution
authority. Existing active getters and mutation admission remain unchanged.

Run Ledger compares the canonical batch result receipt with the observer's exact
operation, call, tool, parent batch, receipt hash, binding hash, outcome and commit
event/version. A legacy transport wrapper may have a different batch identifier;
this does not repair its native invocation history or qualify delivery. A
self-consistent embedded receipt without a persisted owner commit is rejected.
Factory still requires physical provider drain and child-session settlement
before using this barrier and resetting rows through the existing owner APIs.

Independent QA already routes ordinary artifact findings to the exact CE-owned
Director task, requires actual owned repair effects and verifier execution, and
issues a fresh verdict from the current QA runtime. Foreign/ambiguous owners,
stale reports, failed verifiers, provider failures and cancellation remain
blocked. No new QA pipeline or state owner was introduced here.

Component evidence: terminal observer tests include a real owner-gated write,
durable commit, terminal settlement, repeated identical observation and complete
fixture byte-tree immutability. Factory integration closes a real failed lease
with verifier/QA evidence still missing and PASS false. Existing QA owner and
lineage tests pass. Full fresh native product acceptance is still required.

Original `factory_780d3983bf14` read-only replay uses
`KERNELONE_HOME=/home/dains/.polaris/services/main-system`. It observes 17 closed
write commits under the original strict parent/operation streams. The candidate
barrier allows cleanup while PASS remains false and all original gaps remain.
This is component replay evidence; the root operator owns any public recovery.

The existing generated TaskRuntime context pack already omits several prior
public contracts. It was not handwritten or presented as fresh governance
evidence; canonical cell declarations are updated, generated asset refresh and
fresh native qualification remain integration responsibilities.

Native14 exposed a narrower operational mirror defect: canonical and raw read
results differed only in the declared top-level `execution_time_ms`. Cleanup now
compares the complete remaining JSON payload using the existing canonical hash;
status, error, result, invocation identities, nested receipt fields and unknown
extra fields must still match. Real owner-issued mutation fixtures prove the
timing variant can close while nine authority tampering variants remain blocked.

The original native14 turn23 also has a missing control-plane batch projection
and a different hash between its strict committed receipt and lifecycle ref.
That gap remains UNKNOWN and was not reconstructed. Its 26 mutations are closed
under 14 strict parents, including five unchanged-content writes with real
commits. The existing expired-owner recovery API independently checks physical
and child settlement under the exact fence; it does not require a passing
delivery ledger. Recovery is an operator action after current lease expiry.
