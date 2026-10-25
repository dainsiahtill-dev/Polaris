# Installed Java runtime configuration containment

Task: JAVA-SANDBOX-01. Classification: pattern. Status: component verified;
owned source/tests/docs frozen for independent review. Broader qualification and
integration remain Root's work.

## Evidence and hypothesis

Root's synthetic EngineeringTools/K1 execution reached one physical process, then
failed because `java.security` was unavailable. The installed modular Java runtime
contains `jdk.compiler`; absence of the `javac` launcher is a separate host fact.
The actual command `java -m jdk.compiler/com.sun.tools.javac.Main` must be preserved.
No generated target is repaired and no dependency is installed.

Current KernelOne mounts `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64` readonly, plus a
small fixed `/etc` class. Debian/Ubuntu OpenJDK places configuration in
`/etc/java-<major>-openjdk` and links to it from its `/usr/lib/jvm` runtime. Therefore
the installed system runtime is incomplete inside the namespace. Hypothesis:
adding only the matching, trusted system configuration tree resolves that boundary.

## Existing owner and flow

Host persisted permissions/profile -> EngineeringTools -> existing KernelOne
process command owner -> `build_workspace_sandbox_argv` -> bubblewrap -> physical
process observation. The patch stays entirely inside the existing KernelOne
sandbox mount policy. No Cell, owner, controller, schema, ledger, role prompt,
permission expansion, PATH change or target-specific behavior is introduced.

## Proposed policy

1. Discover installed modular JVM homes only under the fixed `/usr/lib/jvm` class.
   Require real, root-owned metadata and executable; neither path nor ancestor may
   be writable by group/others. Do not trust JAVA_HOME, PATH, workspace, model
   output, PermissionScope or a user-supplied extra directory for this discovery.
2. Read trusted `release` as UTF-8; use the modern JAVA_VERSION major (9+) to form
   exactly `/etc/java-<major>-openjdk`.
3. Require the JVM's trusted `conf/security/java.security` symlink to resolve
   exactly to that root's `security/java.security`. Embedded runtime configs need
   no extra mount. Unrelated/vendor/private external configuration is unsupported.
4. Require configuration root, `security/java.security`, and their ancestors to be
   real root-owned paths without group/other write permission; reject symbolic
   links, special files, and writable or user-owned paths on this validated chain.
   Unsafe, broken or unsupported discovered entries add no configuration mount;
   they cannot block another language's contained command. Java may still fail
   with its real missing/unsupported configuration error. No process errors are
   caught or retried outside containment by this optional discovery policy.
   The remaining configuration children share the administrator trust boundary of
   the existing `/usr` mount. Internal symlinks do not authorize more mounts;
   private destinations remain absent. No recursive tree scan runs per command.
5. Mount each deduplicated validated configuration tree at its original absolute
   path with `--ro-bind`. The existing readonly `/` and private mounts remain.

This deliberately adds a small readonly host filesystem surface to the built-in
system runtime class. Unchanged PermissionScope fields do not mean mount scope is
unchanged. The surface contains distro Java configuration only; broad `/etc`, home,
private config targets, unknown distro layouts and writable validated paths stay denied.
Root-owned host package/config mutation concurrent with process construction is
an administrative trust boundary; this is not a guarantee against hostile root.

## Alternatives assessed

- Mount all `/etc`: rejected; exposes unrelated host configuration.
- Add `/etc/java-*` to project read permissions: rejected; mutates task authority
  and still trusts unrelated or malicious roots.
- Follow all JVM conf symlink destinations: rejected; could add home/private roots.
- Override java.security or copy fabricated defaults into the project: rejected;
  changes installed runtime semantics and target bytes.
- Run Java outside containment: rejected; violates mandatory process boundary.
- Mount only `java.security`: smaller but incomplete for policy, jvm.cfg, and other
  distro runtime resources. Matching trusted configuration tree represents an
  installed runtime class; no speculative external destinations are mounted.

## Verification and stopping conditions

Private RED failed on the actual existing builder's missing mount without launching
Java (`red.txt`: one failed, one passed). A second observed RED showed a foreign
Java configuration incorrectly blocking sandboxed Python (`red-liveness.txt`).
Both now pass. Synthetic metadata tests cover accepted exact versioned configuration,
configroot symlink, private/nonstandard target, writable directory/file, user-owned
metadata/config, duplicate JVM aliases and embedded config.
The complete sandbox file passes 59 tests, including real Java module compilation
and execution, unsafe configuration denial, unrelated `/etc` denial, readonly
configuration writes, embedded configuration, child-symlink containment and Python
liveness. Ruff check and format pass. The original synthetic experiment was copied
with only its output bucket changed: final Settings/Profile/EngineeringTools/K1
compile and run each exited zero; empty enabled-tools and empty command each denied
with zero processes. The classfile has `cafebabe` magic. The experiment's four
source hashes stayed unchanged during execution; original failure evidence hashes
remain unchanged. The two processes returned normally through the existing owner
drain contract, and a post-return `/proc/*/cwd` scan found no fixture cwd matches.
That scan is a limited additional observation, not independent exhaustive tracing.
No model calls, native runs or target effects occurred. Never alter target projects,
native run state, host profile permissions, LLM bindings or installed packages.

Stop on any failed containment probe, unexplained source drift, request to broaden
`/etc`, or missing original permission-denial invariants. Java functional success
is proven only for this bounded installed-runtime synthetic case; it does not
prove Bench success or delivery. Root owns broader integration, architecture and
KernelOne release qualification. No new graph edge, public contract, descriptor or
state owner was added; existing process policy and command ownership are reused.
Current graph catalog has no separate entry for `kernelone.process`; this patch
does not invent ownership or repair unrelated graph metadata. Measured discovery
cost on this host: 200 calls, median 2.375 ms; no subprocess or recursive tree scan.

## Primary references

- [Ubuntu Java cryptography configuration](https://ubuntu.com/server/docs/explanation/crypto/java-cryptography-configuration/)
- [Debian OpenJDK 21 packaging rules](https://sources.debian.org/src/openjdk-21/21.0.12.1%2B1-1~deb13u1/debian/rules/)
  explicitly relocate and symlink runtime configuration, including security policy.

## ROOT integration qualification

ROOT independently reran the unchanged synthetic Java compile/run through actual Settings/Profile/EngineeringTools/K1: four cases, two actual processes, both exit0, classfile CAFEBABE and SYNTHETIC_JAVA_HELLO observed; emptytools/emptycommand refused before process. Original first failure and interim liveness RED remain unchanged. ROOT adjacent regression164 passed, architecture40 passed, Ruff/format/mypy exit0, KernelOne ALL12 stages passed with368 passed/1 skipped. Independent review reran59 sandbox tests and a real C++ compile/run with no blocking finding. The stale evidence-pointer nit was corrected to final/report.json. Counts describe separate overlapping suites, not unique new capability proofs.

Evidence: `.polaris/audits/java-runtime-boundary-20261025/ROOT-qualification/`, `ROOT-java-real.py`, `independent-review/report.json`, `handoff.json`. Existing graph owner/public contracts/state ownership remain unchanged; no new Cell or graph truth is needed. This is component qualification for a fresh isolated project, not Java Bench delivery or all120 acceptance. Arbitrary vendor configurations, old Java layouts and GUI/audio quality are unverified until actual project execution.
