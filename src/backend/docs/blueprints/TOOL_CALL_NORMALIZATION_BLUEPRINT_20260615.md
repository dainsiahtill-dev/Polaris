# Tool-Call Normalization Blueprint (2026-06-15)

> Status: Blueprint + incremental implementation ledger (per `src/backend/CLAUDE.md §4.1`).
> Landed items are marked below; unresolved items remain plan-level until code/tests prove them.
> Scope: `src/backend` — `polaris/kernelone/llm/toolkit/**`, `polaris/kernelone/tool_execution/**`,
> `polaris/cells/roles/kernel/internal/**` (parse layer only).
> Authority order: `AGENTS.md` → `docs/AGENT_ARCHITECTURE_STANDARD.md` → graph → this blueprint.

## 0) Core principle (user directive 2026-06-15)

The platform ADAPTS to each LLM's tool-calling habits — especially WEAK models
(`qwen3.6-27b-int4` / gpu variants) whose param conventions differ from mainstream.
We normalize on OUR side. We do NOT emit a teaching error that forces the model to conform
**when a reasonable, lossless, non-destructive normalization could have accepted the call.**

A teaching error is only acceptable when the intent is genuinely ambiguous or the call is
genuinely non-actionable (e.g. pure prose narration with no payload). Every place we currently
REJECT / MISBIND / SILENTLY-DROP a recoverable call is a gap inventoried below.

Fail-closed safety is preserved: normalization never *invents* a destructive intent. Where a
normalization could turn a benign-looking call into something the model did not ask for
(e.g. a whole-file overwrite, a delete), we gate it behind explicit-intent signals
(model-chosen `start`/`end`, target-exists checks, the destructive-shrink predicate).

### 0.1 Implementation ledger

- [LANDED 2026-06-15] Stage-0 JSON/Python-literal object unwrap, nested wrapper unwrap
  (`arguments`, `parameters`, `params`, `input`, `args`, `kwargs`, `tool_input`,
  `tool_arguments`, `tool_args`, `function_arguments`, `function_args`), and single-object-array
  unwrap. Multi-object arrays remain fail-closed.
- [LANDED 2026-06-15] Native provider parsers accept already-decoded dict args and
  `parameters`/`params`/`input`/`args` aliases across OpenAI, DeepSeek, Anthropic, Gemini,
  Ollama, Azure OpenAI, Mistral, Groq, Cohere, Vertex AI, and Bedrock Claude; parsed tool names use
  registry canonicalization.
- [LANDED 2026-06-15] Registered tool-name folding resolves known casing/separator/namespace
  variants such as `Write-File`, `readFile`, `fs.read_file`, `tools.repo-rg`; unknown namespaces
  remain unknown.
- [LANDED 2026-06-15] `execute_command` accepts explicit argv/CommandRequest shapes:
  `argv: [...]`, `args: [...]`, `executable + args`, and scalar `args`.
- [LANDED 2026-06-15] `repo_apply_diff` accepts explicit unified-diff payload aliases
  `patch`, `patch_text`, `unified_diff`, `diff_text` -> `diff`, and no longer emits a flat
  JSON-schema `required: ["diff"]` that contradicts `required_any`. Plain `file+content` remains
  intentionally non-normalized because it is not a diff.

---

## 1) Current architecture (as-built)

```
                    ┌────────────────────────────────────────────────────────────┐
                    │ LLM RESPONSE (native tool_calls | function_call | content)  │
                    └───────────────┬────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┴────────────────────────┐
            │            PARSE LAYER  (extract name + args)   │   << 4 divergent paths, no shared chokepoint >>
            │                                                 │
            │  parsers/native_function.py  parse_openai /      │   parse_openai/deepseek call _parse_json_arguments
            │     parse_anthropic / parse_gemini / ...         │   UNCONDITIONALLY (str(dict)->json.loads fails ->{} )
            │  parsers/core.py  parse_tool_calls               │   auto chain = gemini/ollama/deepseek + openai/anthropic
            │     (text protocol DEAD: `del text` line 131)    │   (cohere/mistral/groq/azure/vertex/bedrock unreached)
            │  parsers/json_based.py  JSONToolParser           │   NOT routed through normalize_tool_arguments
            │  roles/kernel/.../tool_call_protocol.py           │   <- the ONLY path that calls normalize_tool_arguments
            │     extract_text_calls_and_remainder             │
            └───────────────────────┬─────────────────────────┘
                                    │ (name, args dict)
                                    ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ normalize_tool_arguments(tool_name, args)   tool_normalization/__init__.py:87                   │
   │                                                                                                 │
   │  normalize_tool_name()  ── TOOL_NAME_ALIASES + schema alias resolution                          │
   │                                                                                                 │
   │  Stage 1: SchemaDrivenNormalizer.normalize()   schema_driven_normalizer.py:106                  │
   │     • arg_aliases  (spec SSoT)  path-class + other-class mapping                                │
   │     • _normalize_workspace_alias_path  (only for path-canonical keys WITH aliases)              │
   │     • escape_hatch hook (NONE registered today)                                                 │
   │     • _coerce_argument_types -> _coerce_scalar_value  (STRICT: only [str]->str, pure-digit int) │
   │       NOTE: early-return at line 121-122 when tool has no arg_aliases => skips path norm        │
   │                                                                                                 │
   │  Stage 2: TOOL_NORMALIZERS[tool]   normalizers/*.py  (complex transforms only)                  │
   │     • _read_file (offset/limit->start_line/end_line)  • _edit_blocks (list->SEARCH/REPLACE)     │
   │     • _repo_rg / _glob / _search_code (clamp, list flatten)  • execute_command = normalize_noop │
   │     • lenient _coerce_int/_coerce_bool live here (_shared.py) — NOT shared with Stage 1         │
   └───────────────────────┬───────────────────────────────────────────────────────────────────────┘
                                    │ normalized args
                                    ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ executor/core.py  AgentAccelToolExecutor.execute()  line 282                                    │
   │   298 normalize_tool_arguments(...)   <- runs again (idempotent)                                │
   │   315 if not isinstance(arguments, dict): reject "arguments must be an object"                  │
   │   321 _drop_unknown_arguments(spec, args)  <- SEVERS any key not in spec.arguments[]            │
   │   333 _validate_arguments / validate_tool_step  <- required_any groups, _MISSING_ARG_HINTS      │
   │   352 read-before-edit gate (keys on `file`; SKIPPED when file falsy)                           │
   │   391 handler(self, **args)   <- handlers RE-READ aliases defensively (file or path or filepath)│
   └───────────────────────────────────────────────────────────────────────────────────────────────┘

   SCHEMA EMISSION (what the model is TOLD it may send) — 3 divergent builders:
     • _build_tool_spec_from_dict  -> to_openai_function / to_anthropic_tool
         (to_anthropic_tool STRIPS required[] unconditionally, line 71)
     • definitions.create_default_registry  (expands aliases, carries enum/items)
     • tool_helpers._build_contract_native_tool_schema  (expands aliases, DROPS enum/items/min/max)
```

### 1.1 The five structural root causes

1. **No single parse-layer chokepoint.** `tool_call_protocol.extract_text_calls_and_remainder`
   and the native-execution adapter call `normalize_tool_arguments`; `json_based.py` and
   `core.parse_tool_calls` do NOT. The same malformed call is accepted or dropped depending on
   which path it travels.
2. **Strict Stage-1 coercer vs lenient Stage-2 coercer.** `_coerce_scalar_value`
   (schema_driven_normalizer.py:24) accepts only `[str]->str` and pure-digit ints; the lenient
   `_coerce_int/_coerce_bool` (`_shared.py`) live in Stage-2 and only run for tools that happen to
   have a bespoke normalizer. A canonical int sent as `'30s'`/`'5 '`/`'5.0'` is rejected on tools
   using `normalize_noop_args` (execute_command, file_exists, repo_read_slice, ...).
3. **`_drop_unknown_arguments` runs AFTER normalization but BEFORE validation**, and silently
   severs any key not declared in `spec.arguments[]`. This is the mechanism behind the single
   highest-severity gap (`read_file` start_line/end_line) and the write-tool-wall content-synonym
   drops.
4. **Four divergent synonym vocabularies for the same concepts** (spec `arg_aliases`,
   `_edit_blocks._SEARCH_KEYS/_REPLACE_KEYS/_FILE_KEYS/_DIRECT_BLOCK_KEYS`, handler
   `_JSON_EDIT_*_KEYS`, and the inline replace-resolution chain in `_handle_edit_blocks`). They
   disagree, so a synonym accepted in one path is dropped in another.
5. **Three divergent schema builders** advertise different `required[]` / enum / alias sets, so
   what we *tell* the model it may send does not match what we *accept*, and strict client-side
   validators (anthropic-shaped) reject valid forms.

---

## 2) Centralized design — the canonical normalization layer

### 2.1 Where it lives

A single canonical alias+coercion vocabulary, applied **once, before dispatch**, owned by the
existing toolkit (CLAUDE.md §7.1 — reuse `polaris.kernelone.llm.toolkit`, do NOT create a parallel
system). Concretely:

- **`polaris/kernelone/tool_execution/tool_spec_registry.py` `_BUILTIN_REGISTRY` stays the SSoT**
  for tool/arg definitions, `arg_aliases`, `required_any`, enum/items/min/max.
- **New module `tool_normalization/key_vocabulary.py`** holds the ONE canonical synonym set —
  `FILE_KEYS`, `SEARCH_KEYS`, `REPLACE_KEYS`, `BLOCK_KEYS`, `CONTENT_SYNONYMS`, `COMMAND_SYNONYMS`,
  `PATCH_SYNONYMS`, `ARGUMENT_KEYS` — imported by the spec builder, the SchemaDrivenNormalizer,
  the edit-blocks/write handlers, and the parsers. Deletes the four divergent copies.
- **`normalize_tool_arguments` (`tool_normalization/__init__.py`) becomes the SINGLE chokepoint.**
  Every parser funnels its `(name, args)` through it (gap-cluster *parse_layer*). It gains a
  pre-Stage-0 step for JSON-string-wrapped payloads and a unified coercer.
- **Handlers read ONLY canonical keys.** The defensive `file or path or filepath`,
  `kwargs.get('command') or kwargs.get('cmd')`, repo_rg `query/text/search` re-derivation are
  removed once the central layer is trusted (they currently mask drift).

This is centralized, not per-handler: per-handler fallback re-implementation is the bug we are
removing, not the design.

### 2.2 Pipeline (target)

```
normalize_tool_arguments(name, args):
  Stage 0  unwrap JSON-string payloads        (NEW, central)
           • args is a str that json.loads to an object of this tool's param names -> use it
           • a single string arg whose value is self-describing JSON -> unwrap into kwargs
           • {"arguments": "{...}"} nested blob -> unwrap
  Stage 1  schema-driven (existing) + UNIFIED coercer
           • arg_aliases (now drawn from key_vocabulary via the spec)
           • unconditional path-key normalization over every present _PATH_CANONICAL_KEYS key
             (fix early-return-skips-path-norm)
           • _coerce_scalar_value -> routes integer/boolean through lenient _coerce_int/_coerce_bool
           • string body params: list-of-str (len>=1) -> '\n'.join ; command list -> shlex.join
           • enum_aliases (NEW spec field) map value synonyms -> canonical enum member
  Stage 2  per-tool complex transforms (existing, slimmed)
           • range conversion, clamp-from-spec (min/max read from arg spec, deletes copy-paste)
           • edit_blocks block-shape coercion (now ALSO handles flat 'key: value' string + bare code)
  Stage 3  intent reclassification guard (NEW, central, FAIL-CLOSED)
           • detect whole-file-create intent (search empty + target missing) -> route to write_file
           • detect implicit single-target file (file=None + start/end + exactly ONE fresh-read
             file in window) -> bind that file; otherwise teaching error (ambiguous)
```

### 2.3 What conventions the layer accepts

| Convention | Accept rule | Fail-closed guard |
|---|---|---|
| **Param aliases** | `arg_aliases` + `key_vocabulary` synonym sets, single SSoT | canonical-first when both present and non-empty (prefer first NON-EMPTY) |
| **Content body synonyms** | `text/body/code/source/file_content/contents/data/value/new_content -> content` for write_file/append/edit_file | — |
| **JSON-string-vs-object** | Stage 0 unwraps a stringified args object / single self-describing JSON arg | only when parsed object's keys ⊆ this tool's param/alias names |
| **Type coercion** | one lenient coercer both stages: `'5 '`/`'+5'`/`'5.0'`/`'30s'`->int; `yes/on/1/Y`->bool | reject genuinely non-numeric; never coerce a list of >1 ints to a scalar |
| **List-vs-string** | `[str]`->str; `['l1','l2']`->`'\n'.join` (body) / `shlex.join` (command); `[ 'foo' ]`->str for glob/pattern | join order preserved; command argv joined verbatim |
| **Fenced / escaped payloads** | strip ```` ``` ```` fence + `\n`-escape repair on write_file/append `content` (reuse `_normalize_block_input`) | escape repair only when payload has NO real newlines (existing conservative guard) |
| **Single-target file fallback** | file=None + start/end -> most-recently-read file from `_file_read_history` | ONLY when EXACTLY ONE file is in the fresh-read window; else teaching error |
| **Whole-file dumped into edit_blocks** | markerless bare code + target exists + `_looks_like_complete_file_replacement` -> whole-file overwrite | target must EXIST; bare code that looks partial -> prose teaching error |
| **Enum synonyms** | `enum_aliases` map (`find/search/where->locate`) before cell contract validates | unknown value -> default to safest member or teaching error, never crash |
| **Tool-name variants** | hyphen/dot/camelCase -> snake_case fold before `^[a-z][a-z0-9_]{0,63}$` gate, then alias resolve | unknown after fold -> drop, not bind to phantom |

### 2.4 Fail-closed invariants (non-negotiable)

1. A normalization that could change the **blast radius** of an edit (whole-file overwrite, delete,
   destructive shrink) fires ONLY on explicit-intent signals: model-chosen `start`/`end`, an
   existing target, an empty replacement over an explicit line range. The destructive-shrink
   predicate is centralized (one predicate, `intent_is_explicit` flag) and still blocks
   inferred/fuzzy whole-file shrinks.
2. The implicit-single-target fallback requires EXACTLY ONE fresh-read file; ambiguity -> teaching
   error, never a guess. Binding the implicit file also re-arms the read-before-edit gate (today it
   is silently skipped when `file` is falsy — a real safety hole).
3. Stage 0 JSON unwrap only triggers when the parsed object's keys are a subset of the tool's
   own param/alias names — so a literal JSON string the model means to WRITE is never unwrapped.
4. UTF-8 only, explicit (CLAUDE.md §5). Encoding tokens are canonicalized
   (`UTF8/utf_8/utf-8 -> utf-8`, `ascii` accepted as subset) rather than string-equality rejected.
5. No business/target-project code (CLAUDE.md §8). The extensionless allowlist is widened to
   *well-known generic* filenames (LICENSE/CHANGELOG/Procfile/...), not project-specific names.

---

## 3) Gap inventory grouped by tool family

Counts: 60 gaps total. Each maps to a P-item in §4. `(sev)` = auditor severity.

### 3.1 write_file / append_to_file / edit_file (write family) — 9 gaps
- **content body under synonym** `text/body/code/source/file_content/data/value/new_content`
  silently dropped by `_drop_unknown_arguments`; `write_file.arg_aliases` is the identity no-op
  `{"content":"content"}` (tool_spec_registry.py:966). **(high — #1 write-tool wall)**
- append_to_file body under `text/append/data` rejected (no aliases; handler reads only
  `kwargs.get('content')`, filesystem.py:1937). **(high)**
- content fence-wrapped ```` ```python ... ``` ```` written verbatim -> pre-write syntax gate
  hard-rejects (filesystem.py:720-742). **(high)**
- content with literal `\n` (single-line JSON artifact) written verbatim -> syntax gate reject. **(med)**
- edit_file line-range content under `new_text/replacement/code` dropped. **(med)**
- encoding spelling variant `UTF8`/`utf_8`/`ascii` hard-rejected (filesystem.py:602). **(low)**
- extensionless `LICENSE/CHANGELOG/Procfile/...` rejected as missing-extension (filesystem.py:626-646). **(low)**
- edit_blocks lone `start` (no `end`) / 0-based lines falls through to prose parser and rejects
  (filesystem.py:1311-1313). **(low)**
- precision_edit/search-replace on a non-existent file (create-intent) returns "requires search". **(low)**

### 3.2 edit_blocks (the dominant weak-model wall) — 14 gaps
- line-range edit with `start+end+replace` but NO `file`, file derivable from fresh-read history. **(high)**
- JSON line-range payload with `file` omitted on every item. **(high)**
- blocks as flat `key: value` YAML-ish STRING (`file_path: x start_line: 1 ... new_text: ...`),
  live in L3-16 / L2-09..12 logs — parses as neither JSON nor markers. **(high)**
- blocks = bare whole-file source, target EXISTS, no markers/range (48/116 prose-rejections). **(high)**
- read/edit on a from-scratch leaf name (`main.js`, `style.css`) before it is written -> generic
  not-found, no "create with write_file" redirect (read handlers lack the redirect edit_blocks has). **(high)**
- replacement under unchecked key `after/new/updated/to/target` at top level. **(med)**
- `content`->blocks alias collides with content-as-replacement intent in line-range mode. **(med)**
- SEARCH text whitespace/indent drift -> "No match" dead-end (no anchor/line-range fallback). **(med)**
- legitimate large condensing rewrite trips destructive-shrink gate on EXPLICIT range. **(med)**
- empty replacement over explicit range (delete intent) rejected (filesystem.py:1188-1197). **(low)**
- blocks nested under `text/edit/value` synonym container (JSON path only unwraps `blocks`). **(low)**
- partially-escaped multi-line payload keeps literal `\n` (inherent ambiguity, document as residual). **(low)**
- narration in blocks alongside present `start/end` -> drop narration, use line-range. **(low)**
- arg-name-as-path-value (`file='filepath'`) -> "File not found: filepath" (placeholder detect). **(low)**

### 3.3 read_file / repo_read_* / search (read & search) — 7 gaps
- **`read_file` start_line/end_line not in `spec.arguments[]`** -> normalizer produces them, handler
  consumes them, `_drop_unknown_arguments` severs them -> every ranged read degrades to full-file
  read -> trips budget guards. (tool_spec_registry.py:937-941). **(high)**
- scout_probe mode synonym `find/search/explore/map` rejected (only `locate`/`boundary`). **(med)**
- repo_apply_diff given `file`+`content` (full body) instead of unified diff is intentionally not
  aliased to `diff`; explicit diff payload aliases are now accepted (`patch`/`patch_text`/
  `unified_diff`/`diff_text` -> `diff`). Residual gap: improve the teaching error when a full body
  is sent to a diff-only tool. **(med / fail-closed by design)**
- repo_rg `glob` as `['*.py']` list not coerced (only string-declared single-element list is). **(low)**
- repo_read_* canonical `file` skips path-normalization when tool has no triggering alias. **(low)**
- file_exists double-filled `file`+`path` discards the non-empty alias when canonical is empty. **(low)**
- read_file `max_bytes` as `'200000 # whole file'` raises inside handler (not coerced). **(low/crash)**

### 3.4 execute_command — 7 gaps
- **command as argv LIST** `['npm','install']` rejected (only len==1 list unwrapped). **(high)**
- command under invented key `args/argv/cmdline/shell/script/commands/input/code` dropped. **(high)**
- argv split across `executable`+`args` (CommandRequest shape) dropped. **(med)**
- command as a dict `{cmd, cwd}` rejected by StringValidator. **(med)**
- timeout verbal `'60s'/'2 min'/'30.0'` rejected. **(low)**
- shell-prompt sigil `'$ npm install'` / `bash -c "..."` survives sanitize, fails whitelist. **(med)**
- empty/null command: divergent errors (handler "Missing command" vs contracts "missing required
  argument") + no `_MISSING_ARG_HINTS` entry for execute_command. **(low)**
- leading `cd <dir> &&` for a non-readonly command rejected (only ls/find honor cd-alias). **(med)**

### 3.5 Parse layer (native + textual) — 11 gaps
- **textual tool call in content** (`[TOOL_CALL]{...}` / bare JSON) silently dropped — the built
  `CanonicalToolCallParser`/`JSONToolParser` are not wired for execution (`del text`, core.py:131). **(high)**
- **OpenAI/DeepSeek `function.arguments` as already-decoded dict** -> `_parse_json_arguments`
  stringifies then json.loads fails -> `{}` (native_function.py:68,258). **[LANDED 2026-06-15]**
- Anthropic `input` as JSON string dropped to `{}` (line 109,115). **[LANDED 2026-06-15]**
- args under `parameters/input/params` not read by native parsers (only `arguments`).
  **[LANDED 2026-06-15 for native provider parsers; text/json parser chokepoints remain]**
- arguments JSON parses to non-object (bare list/string) discarded entirely. **(med)**
- json_based: args spread as sibling top-level keys, or arguments string decoding to dict, ignored. **(med)**
- cohere/gemini-string shapes unreachable under `provider='auto'` (partial auto chain). **(med)**
- tool name uppercase/hyphen/dot (`Write-File`, `fs.write_file`) fails regex gate, dropped. **(med)**
- text parsers (`json_based`, `core`) return raw args with NO `normalize_tool_arguments` pass. **(med)**
- XML-tag tool extraction binds prose `<note>` to phantom tools when no whitelist passed. **(low)**
- smart/single-quote repair only fires when whole payload has zero double-quotes. **(low)**

### 3.6 bind / dispatch (shared coercion + boundary) — 12 gaps
- **strict Stage-1 int coercer** rejects `'5 '/'+5'/'5.0'/'30s'/'line 5'` on noop-normalizer tools. **(high)**
- **required string body as line ARRAY** (content/blocks/command) rejected by StringValidator. **(high)**
- execute_command argv array rejected (duplicate of §3.4, central fix). **(med)**
- boolean `yes/on/1/Y` rejected by 2-literal Stage-1 bool check. **(med)**
- inner SEARCH/REPLACE alias keys (`old_string/new_string/before/after`) only known inside
  edit_blocks list-coercion, not for precision_edit/search_replace/edit_file. **(high)**
- Anthropic `str_replace` convention (`command/old_str/new_str`) unbound. **(med)**
- misnamed canonical not in any alias table (`filename->file`, `name->file`, `regex->pattern`)
  silently dropped. **(med)**
- read_file `offset=0` (0-based) discarded by `>0` guard. **(low)**
- arguments as JSON STRING at dispatch boundary -> `{}` -> missing-required (duplicate of Stage 0). **(high)**
- repo_apply_diff vs apply_patch mirror-image alias directions misbind. **(low; repo_apply_diff
  direction landed for explicit diff aliases, apply_patch unification remains P4)**
- empty-string/null optional int validated as junk instead of falling back to default. **(med)**
- handler-level alias re-implementation duplicates SSoT and masks drift. **(centralization)**

### 3.7 definitions / schema emission — 10 gaps
- content-synonym aliases absent from `_BUILTIN_REGISTRY` write tools (advertise + resolve). **(high)**
- arguments-as-JSON-string lost to `{}` in `normalize_tool_arguments` (Stage 0 home). **(high)**
- `to_anthropic_tool` strips `required[]` unconditionally (line 71) -> model never told mandatory args. **(high)**
- repo_apply_diff `diff` in flat `required[]` contradicts `required_any` -> strict client rejects patch-only.
  **[LANDED 2026-06-15]**
- contract-native schema builder drops `enum` -> model guesses closed-set values. **(med)**
- array `items` hardcoded `{type:string}`; scalar->array tolerance not advertised. **(low)**
- repo_rg over-advertises 5+ alias properties -> model double-fills conflicting values. **(low)**
- edit_blocks line-range form under-specified by single `required_any=[['blocks','start']]`. **(med)**
- execute_command description lacks "do not use to write files" steering note. **(low)**
- three schema builders diverge on alias-advertising / enum / required-minimization. **(centralization)**

---

## 4) Prioritized plan (P1..Pn)

Effort: **S** = ≤0.5 day, **M** = 1-2 days, **L** = 3-5 days. Order = friction-removed per effort,
highest first. Each lands behind unit tests (CLAUDE.md quality gates) and a CI argument-coverage gate.

### P1 — Central content-synonym + the wire-severing spec fixes  *(M)*  [QUICK WINS bundle]
**Removes the #1 write-tool wall + the highest-severity read regression in one declarative pass.**
- Declare `CONTENT_SYNONYMS` in `write_file`/`append_to_file`/`edit_file` `arg_aliases`
  (`text/body/code/source/file_content/contents/data/value/new_content -> content`).
- Add `start_line`,`end_line` (int, optional) to `read_file.arguments[]` so the normalizer's output
  survives `_drop_unknown_arguments` (kills the silent ranged-read->full-read degrade).
- Add content/replace synonyms to `edit_file` line-range; add `command` synonyms
  (`script/args/argv/cmdline/shell_command/commands/command_line -> command`) to `execute_command`.
Files: `tool_spec_registry.py` (`_BUILTIN_REGISTRY`), new `tool_normalization/key_vocabulary.py`.

### P2 — Unify the coercer (one lenient coercer, both stages)  *(M)*
- Route `_coerce_scalar_value` integer/boolean through `_shared._coerce_int/_coerce_bool`
  (strip whitespace/units, `+5`, `5.0`, `yes/on/1/Y`).
- Add string-body list-join: `['l1','l2']->'\n'.join` (content/blocks), `shlex.join` (command);
  generalize `[str]->str` to glob/pattern.
- Drop empty-string/None for OPTIONAL args before validation (let spec default apply).
Files: `schema_driven_normalizer.py`, `normalizers/_shared.py`.

### P3 — Stage 0 JSON-wrapped payload unwrap (one place)  *(S)*
- In `normalize_tool_arguments`: detect a stringified args object / single self-describing JSON
  arg / nested `{"arguments":"{...}"}`; unwrap only when parsed keys ⊆ tool param/alias names.
- Mirror at `core.py:execute` boundary (json.loads before the `isinstance(dict)` reject).
Files: `tool_normalization/__init__.py`, `executor/core.py`.

### P4 — Collapse the four synonym vocabularies into key_vocabulary  *(M)*
- `FILE_KEYS/SEARCH_KEYS/REPLACE_KEYS/BLOCK_KEYS/CONTENT_SYNONYMS/PATCH_SYNONYMS/ARGUMENT_KEYS`
  in one module; consumed by spec builder, SchemaDrivenNormalizer, `_edit_blocks`, write/edit
  handlers, parsers. Lift inner SEARCH/REPLACE aliases (`old_string/new_string/before/after`) into
  `arg_aliases` for precision_edit/search_replace/edit_file. Unify patch param across
  repo_apply_diff/apply_patch. `repo_apply_diff` explicit patch/diff aliases are already landed;
  the shared vocabulary module and `apply_patch` side remain open.
Files: `key_vocabulary.py`, `_edit_blocks.py`, `filesystem.py`, `repo.py`, `tool_spec_registry.py`.

### P5 — edit_blocks payload classifier + create-intent redirect  *(L)*
- One `classify_edit_blocks_payload(args, executor) -> {line_range|search_replace|whole_file_overwrite
  |needs_write_file|prose}` that also heals the flat `key: value` STRING form and the markerless
  bare-whole-file form (target-exists gated). Shared not-found-on-create-intent helper used by
  edit_blocks AND read handlers (repo_read_*/read_file) to redirect leaf reads-before-write to
  write_file. Implicit single-target file fallback (re-arms read-before-edit gate). Centralize the
  destructive-shrink predicate with `intent_is_explicit`; accept empty-replacement deletes on
  explicit ranges. SEARCH no-match -> line-range/anchor fallback.
Files: `filesystem.py`, `executor/core.py`, new `tool_normalization/edit_intent.py`.

### P6 — Parse-layer single chokepoint + native dict-args fix  *(M)*
- [LANDED 2026-06-15] Branch native provider parsers through shared decoded-object / single-object-array
  / JSON-or-Python-literal argument parsing; run Anthropic string `input` through
  `_parse_json_arguments`; add `parameters/input/params/args` arg-key fallback across the native
  provider parser set.
- Remaining: route `_parse_json_arguments` through the lenient `parse_lenient_json_object`.
- Funnel EVERY parser's `(name,args)` through `normalize_tool_arguments` (+ `normalize_tool_name`
  fold step). Complete the `provider='auto'` parser registry.
Files: `parsers/native_function.py`, `parsers/json_based.py`, `parsers/core.py`,
`roles/kernel/internal/output_parser.py`.

### P7 — Re-enable text-protocol execution as fail-open fallback  *(M)*
- When native `tool_calls` empty but `clean_content` non-empty, run
  `CanonicalToolCallParser.extract_text_calls_and_remainder` + `JSONToolParser` (already built and
  tested) as a centralized fallback. Gate XML-tag extraction to known tool names when no whitelist.
Files: `parsers/core.py`, `roles/kernel/internal/output_parser.py`, `tool_call_protocol.py`.

### P8 — Enum/value-synonym normalization + scout/command shape repair  *(S)*
- `enum_aliases` spec field consumed by SchemaDrivenNormalizer before cell contract validates
  (`find/search->locate`, etc.). execute_command normalizer: list-argv->join, dict->command,
  executable+args reassembly, shell-sigil/`bash -c` unwrap, `cd <dir> &&`->cwd.
Files: `tool_spec_registry.py`, `schema_driven_normalizer.py`, new `normalizers/_execute_command.py`,
`executor/handlers/command.py`, `executor/handlers/scout.py`.

### P9 — One schema-emission function (advertise == enforce)  *(M)*
- Single `_arg_to_jsonschema(arg)` mapping ALL declared constraints (enum/items/min/max/pattern),
  shared by OpenAI/Anthropic/contract-fallback builders. Derive advertised `required[]` from
  `required_any` (single-key->required, multi-key->anyOf/description); stop unconditionally
  stripping required in `to_anthropic_tool`. Collapse alias over-advertising to canonical-property +
  "also accepts" hint.
Files: `tool_spec_registry.py`, `definitions.py`, `tool_helpers.py`.

### P10 — CI argument-coverage gate + handler de-duplication  *(S)*
- Extend `_assert_contracts_sync` to assert: every key a handler reads via `kwargs.get(...)` is in
  `spec.arguments` or an `arg_alias`; every key a normalizer emits is in `spec.arguments`. Then
  remove handler-level alias re-reads (handlers receive only canonical keys). Encoding-token
  canonicalization, extensionless allowlist widening, placeholder-path detection, unified
  execute_command error + `_MISSING_ARG_HINTS` entry.
Files: `normalizers/__init__.py`, `filesystem.py`, `command.py`, `contracts.py`,
new `docs/governance/ci/scripts/run_tool_arg_coverage_gate.py`.

---

## 5) Risks & boundaries

1. **Over-normalization eroding fail-closed safety.** Highest risk in P5 (whole-file overwrite /
   implicit-file binding). Mitigation: explicit-intent gates (target-exists, exactly-one-fresh-read,
   model-chosen start/end), centralized destructive-shrink predicate, and the implicit-file binding
   MUST re-arm the read-before-edit gate (do not bypass it).
2. **Stage 0 JSON unwrap eating a legitimate JSON-string the model wants to WRITE.** Mitigation:
   only unwrap when parsed keys ⊆ tool param/alias names; never unwrap a write_file `content` whose
   string happens to be JSON.
3. **Conftest/mock drift (per memory `weak-model-harness-hardening`).** Several normalizer tests
   monkeypatch the registry; the new key_vocabulary import path and the schema-builder collapse can
   produce false-green/false-red. Run the full `tool_normalization` + `tool_execution` test
   directories, not just touched files.
4. **`_assert_contracts_sync` is currently import-time-disabled** (commented at
   normalizers/__init__.py:142, runs only in CI). The new arg-coverage assertion must follow the
   same staged rollout (audit-only first) to avoid blocking unrelated dev.
5. **Parser funnel double-normalization.** `core.py:execute` already calls
   `normalize_tool_arguments` (line 298); funneling parsers through it too means args are normalized
   twice. Normalization must stay idempotent (it is today — alias maps are stable; verify the new
   Stage 0/coercer steps are idempotent under unit test).
6. **Three schema builders are consumed by different live paths** (OpenAI FC, anthropic-native,
   contract-fallback). Collapsing them (P9) touches the `tool_calling_canonical_gate` surface
   (`src/backend/CLAUDE.md §7.4`). Coordinate with that gate; do not change advertised tool *names*.
7. **Scope discipline.** No business/target-project code (CLAUDE.md §8): extensionless allowlist and
   command steering stay generic. UTF-8 explicit everywhere (CLAUDE.md §5).

---

## 6) Centralization summary (one line)

Collapse four divergent synonym vocabularies, two divergent coercers, three divergent schema
builders, and four un-funneled parsers into ONE canonical key_vocabulary + ONE lenient coercer +
ONE schema-emission function + ONE parse->normalize chokepoint, applied before dispatch, with
fail-closed intent gates so adaptation never invents a destructive call.

---

## 7) Graph / Cell ownership map (implementation constraint)

This refactor must NOT create a new Cell. It is a KernelOne technical capability consumed by the
LLM and role Cells through their existing public execution surfaces.

| Concern | Current/target owner | Allowed touched paths | Boundary rule |
|---|---|---|---|
| Tool spec SSoT | `kernelone.tool_execution` | `polaris/kernelone/tool_execution/tool_spec_registry.py` | Tool names, canonical args, `required_any`, enum/items/min/max remain here. |
| Canonical normalization | `kernelone.llm.toolkit` | `polaris/kernelone/llm/toolkit/tool_normalization/**` | Owns alias vocabulary, coercion, Stage 0 unwrap, idempotency. |
| Parser adaptation | `kernelone.llm.toolkit` + `roles.kernel` parse adapter | `polaris/kernelone/llm/toolkit/parsers/**`, `polaris/cells/roles/kernel/internal/**` parse-only paths | Role code may call canonical normalizer; it must not define its own synonym vocabulary. |
| Tool execution hard gates | `kernelone.llm.toolkit.executor` | `polaris/kernelone/llm/toolkit/executor/**` | Read-before-edit, allowed-tools, validation, and effect dispatch stay executor-owned. |
| Tool handlers | `kernelone.llm.toolkit.executor.handlers` | `polaris/kernelone/llm/toolkit/executor/handlers/**` | Handlers consume canonical keys only after P10; before that, compatibility reads are audit targets. |
| LLM tool orchestration | `llm.tool_runtime` Cell | `polaris/cells/llm/tool_runtime/**` | Should observe normalized calls/results, not duplicate parse or binding logic. |
| Role runtime | `roles.kernel` / `roles.runtime` Cells | `polaris/cells/roles/**` public/internal parse edges only | No cross-Cell import of another Cell internal implementation. |

Graph implications:

1. No new `docs/graph/catalog/cells.yaml` Cell is required.
2. If new public contracts are introduced, they must be KernelOne contracts or existing Cell public
   contracts; do not add a second tool-spec registry.
3. Normalization itself is pure. File, process, network, and KFS effects remain in the executor and
   existing handlers. Receipts/metrics introduced by this blueprint are observability effects, not
   tool effects.
4. `polaris/kernelone/llm/tools/normalizer.py` is a compatibility shim. It must delegate to
   `polaris.kernelone.llm.toolkit.tool_normalization` and may not carry independent alias/coercion
   rules.

---

## 8) Execution-grade release plan

The original P1..P10 list is the target backlog. Land it in the following slices so the system gains
adaptation without weakening safety.

### Slice 0 - Audit foundation, no behavior change

Goal: make drift visible before changing acceptance behavior.

- Add `tool_normalization/key_vocabulary.py` with canonical synonym constants, but do not wire every
  consumer yet.
- Add a golden corpus file:
  `polaris/kernelone/llm/toolkit/tests/fixtures/tool_call_normalization_corpus.jsonl`.
- Add a corpus runner test that can operate in two modes:
  - `expected_current`: documents current reject/drop behavior.
  - `expected_target`: documents desired normalized behavior.
- Add `ToolNormalizationReceipt` as an in-memory/audit structure behind the normalizer boundary.
  It records what changed without persisting full file bodies.
- Add an audit-only arg coverage script:
  `docs/governance/ci/scripts/run_tool_arg_coverage_gate.py --mode audit`.

Exit criteria:

- Current behavior is reproducible from corpus.
- Every P1..P10 gap maps to at least one corpus row.
- Coverage script reports drift but does not fail CI yet.

### Slice 1 - Non-destructive argument survival

Goal: remove the silent drops that directly block weak models, while avoiding intent
reclassification.

Includes: P1, P2, P3, and the native dict-args part of P6.

Allowed behavior changes:

- Content synonyms bind to `content`/`diff`/`command`.
- `read_file` range args survive `_drop_unknown_arguments`.
- JSON-string argument objects unwrap only when keys match this tool's param/alias namespace.
- Scalar/list coercion accepts common weak-model shapes.

Not allowed yet:

- Whole-file overwrite inference.
- Implicit file binding from history.
- Text-protocol fallback execution.

Exit criteria:

- All non-destructive corpus rows pass.
- Safety corpus rows still reject.
- Idempotency test proves `normalize_tool_arguments(name, normalize_tool_arguments(name, args))`
  equals a single pass for every corpus row.

### Slice 2 - Parser chokepoint and schema parity

Goal: make advertised tool schemas and accepted runtime shapes match.

Includes: remaining P6 plus P9.

Allowed behavior changes:

- Native parsers accept already-decoded dict args and provider-specific `input/params/parameters`.
- Every parsed `(tool_name, args)` flows through canonical tool-name and argument normalization.
- Schema builders share one arg-to-JSON-schema mapper.

Exit criteria:

- OpenAI/DeepSeek/Anthropic/Gemini/Ollama parser tests prove the same logical call normalizes to the
  same canonical `{name, args}`.
- Schema emission tests prove `enum/items/min/max/required_any` survive across OpenAI,
  Anthropic, and contract-native builders.
- No advertised tool name changes.

### Slice 3 - Intent-aware edit recovery, gated

Goal: fix the dominant `edit_blocks` weak-model wall without guessing destructive actions.

Includes: P5.

Rollout guard:

- Introduce a mode flag, initially defaulting to audit-only:
  `KERNELONE_TOOL_NORMALIZATION_INTENT_MODE=audit|enforce`.
- In `audit`, receipts report what would have been rebound/reclassified, but executor behavior stays
  unchanged.
- In `enforce`, only the invariants in Section 2.4 may trigger behavior changes.

Allowed behavior changes in enforce:

- `start/end/replace` line-range payloads can bind a file only when exactly one fresh read is in the
  executor window.
- Explicit empty replacement over an explicit range is accepted as delete intent.
- Bare whole-file payload can become overwrite only when the target exists and the classifier proves
  complete-file intent.
- Read-before-edit is re-armed after implicit binding.

Exit criteria:

- Ambiguous multi-file history corpus rejects with a teaching error.
- Not-read edit corpus remains blocked by read-before-edit.
- Whole-file shrink safety corpus rejects unless explicit-intent predicate is true.

### Slice 4 - Text fallback and handler de-duplication

Goal: accept recoverable text calls only after the canonical native path is stable.

Includes: P7, P8, P10.

Rules:

- Text fallback runs only when native tool calls are empty.
- XML/tag extraction must be whitelisted to known tool names.
- Handler-level alias reads are removed only after coverage gate has been clean in audit mode.
- Coverage gate moves from `--mode audit` to `--mode enforce` after the handler cleanup.

Exit criteria:

- Text fallback corpus accepts `[TOOL_CALL]`/bare JSON tool calls without binding prose tags.
- Handler coverage gate has zero unregistered read keys and zero normalizer-emitted unknown keys.
- `_MISSING_ARG_HINTS` and executor validation produce one canonical error style.

---

## 9) Golden corpus design

The corpus is the contract between audit findings and implementation. It prevents this refactor from
becoming a collection of anecdotal fixes.

### 9.1 Corpus row schema

```json
{
  "id": "write_file.content_text_alias",
  "phase": "P1",
  "tool": "write_file",
  "provider_shape": "native_openai|native_anthropic|text_json|executor_direct",
  "raw_name": "write_file",
  "raw_args": {"file": "src/app.js", "text": "console.log('ok')"},
  "preconditions": {
    "workspace_files": [],
    "fresh_reads": []
  },
  "expected_target": {
    "tool": "write_file",
    "args": {"file": "src/app.js", "content": "console.log('ok')"},
    "accepted": true,
    "receipt_codes": ["alias:content:text"]
  },
  "safety_class": "non_destructive"
}
```

### 9.2 Required corpus groups

| Group | Minimum rows | Must include |
|---|---:|---|
| Write body aliases | 8 | `text`, `body`, `code`, `source`, `file_content`, fenced body, escaped `\n`. |
| Read/search ranges | 6 | `offset/limit`, `start/end`, `start_line/end_line`, bad optional int. |
| Command shapes | 8 | argv list, dict `{cmd,cwd}`, `executable+args`, timeout units, prompt sigil. |
| Native provider args | 10 | OpenAI dict args, DeepSeek dict args, Anthropic string input, Gemini/Ollama response. |
| Text fallback | 6 | `[TOOL_CALL]`, bare JSON, XML whitelisted, XML prose false positive. |
| Edit blocks | 15 | line range, missing file with one fresh read, missing file with two fresh reads, YAML-ish string, bare whole file, partial bare code. |
| Safety abuse | 10 | JSON content not unwrapped, ambiguous file binding, inferred delete, destructive shrink, unknown tool-name fold. |

Every corpus row must specify whether the desired outcome is `accepted`, `teaching_error`, or
`dropped_unknown_tool`. Silent drop is not a valid target outcome for a known tool with recoverable
args.

---

## 10) Normalization receipt and telemetry

Add a receipt so production failures can be reconstructed without logging full user code.

### 10.1 Receipt fields

```python
@dataclass(frozen=True)
class ToolNormalizationReceipt:
    tool_name_raw: str
    tool_name_canonical: str
    input_shape: str
    normalized: bool
    accepted: bool
    safety_class: str
    changes: tuple[str, ...]
    dropped_keys: tuple[str, ...]
    teaching_error: str | None
    raw_args_sha256: str
    normalized_args_sha256: str
```

Rules:

1. Do not store full `content`, `diff`, `replacement`, or `command` bodies in receipts.
2. Store key names, type transitions, short scalar previews, and SHA-256 hashes.
3. Receipt codes are stable strings, for example:
   `alias:content:text`, `coerce:int:timeout`, `unwrap:arguments_json`,
   `guard:ambiguous_implicit_file`, `reject:destructive_shrink`.
4. Executor errors should include the receipt id/hash when a teaching error is generated, so replay
   can connect model output -> normalizer decision -> executor result.

### 10.2 Metrics

Track counters by role, provider, model, and tool:

- `tool_normalization.accepted_total`
- `tool_normalization.changed_total`
- `tool_normalization.teaching_error_total`
- `tool_normalization.safety_reject_total`
- `tool_normalization.dropped_unknown_key_total`
- `tool_normalization.intent_reclassification_total`
- `tool_normalization.text_fallback_total`

These metrics are production stability signals. A spike in `teaching_error_total` for one model
means the vocabulary still does not match that model's conventions. A spike in
`safety_reject_total` after P5 means the classifier is seeing risky edit shapes and should stay in
audit mode for that path.

---

## 11) Test and gate matrix

### 11.1 Unit tests

| Test file | Coverage |
|---|---|
| `polaris/kernelone/llm/toolkit/tests/test_tool_call_normalization_corpus.py` | Runs golden corpus through Stage 0/1/2 normalization. |
| `polaris/kernelone/llm/toolkit/tests/test_tool_normalization_idempotency.py` | Double-normalization invariants. |
| `polaris/kernelone/llm/toolkit/tests/test_tool_normalization_receipts.py` | Receipt redaction and stable codes. |
| `polaris/kernelone/llm/toolkit/tests/test_tool_parser_normalization_chokepoint.py` | Native/text parser funnels produce canonical calls. |
| `polaris/kernelone/tool_execution/tests/test_tool_arg_coverage_gate.py` | Spec args, aliases, normalizer output, and handler reads agree. |
| `polaris/kernelone/llm/toolkit/tests/test_tool_schema_emission_parity.py` | OpenAI/Anthropic/contract builders emit equivalent constraints. |
| `polaris/kernelone/llm/toolkit/tests/test_edit_intent_classifier.py` | P5 safety and recovery decisions. |

### 11.2 Required commands per slice

```bash
cd src/backend
python -m ruff check polaris/kernelone/llm/toolkit polaris/kernelone/tool_execution polaris/cells/roles/kernel --fix
python -m ruff format polaris/kernelone/llm/toolkit polaris/kernelone/tool_execution polaris/cells/roles/kernel
python -m mypy polaris/kernelone/llm/toolkit polaris/kernelone/tool_execution polaris/cells/roles/kernel
python -m pytest -q \
  polaris/kernelone/llm/toolkit/tests \
  polaris/kernelone/tool_execution/tests \
  polaris/tests/test_output_parser_patch_file.py
python docs/governance/ci/scripts/run_tool_arg_coverage_gate.py --mode audit
```

For Slice 3 enforce mode:

```bash
cd src/backend
KERNELONE_TOOL_NORMALIZATION_INTENT_MODE=enforce python -m pytest -q \
  polaris/kernelone/llm/toolkit/tests/test_edit_intent_classifier.py \
  polaris/kernelone/llm/toolkit/tests/test_tool_call_normalization_corpus.py \
  polaris/tests/test_output_parser_patch_file.py
```

For final enforcement:

```bash
cd src/backend
python docs/governance/ci/scripts/run_tool_arg_coverage_gate.py --mode enforce
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

---

## 12) High-risk design decisions that must be explicit in code review

1. **Unknown keys are evidence, not noise.** `_drop_unknown_arguments` may still filter before
   handler dispatch, but every dropped key from a known tool must be visible in the receipt. Silent
   severing is the bug class this blueprint removes.
2. **Canonical key wins only when non-empty.** If both `file=""` and `path="src/a.py"` appear, the
   non-empty alias must be allowed to supply the canonical value. Empty canonical fields must not
   shadow useful alias values.
3. **Schema aliases are not schema advertising spam.** Runtime may accept broad aliases, but emitted
   schemas should prefer canonical properties with a concise "also accepts" hint. Over-advertising
   many alias fields causes weak models to double-fill conflicting keys.
4. **Parser fallback is subordinate to native calls.** Text fallback is never allowed to add calls
   when native calls are already present unless a future ADR explicitly defines merge semantics.
5. **Intent reclassification is not normalization.** Alias/coercion preserves intent. P5 changes
   execution class (`edit_blocks` -> `write_file`, missing file -> implicit file). That requires a
   separate classifier, receipts, flags, and stronger tests.
6. **No target-project special cases.** Extensionless filename allowlists may include only generic
   well-known names. Do not add application-specific file names, frameworks, or product terms.
7. **The old normalizer shim must shrink.** Any bug fixed in
   `polaris/kernelone/llm/toolkit/tool_normalization` must not be re-fixed in
   `polaris/kernelone/llm/tools/normalizer.py`; the latter delegates and eventually disappears.

---

## 13) Definition of done

The refactor is complete only when all of the following are true:

1. The 60 inventoried gaps have corpus coverage and either pass target behavior or are explicitly
   documented as residual risk.
2. No parser path can return a known tool call without passing through canonical tool-name and
   argument normalization.
3. Normalization is idempotent across the full corpus.
4. `read_file` ranged reads preserve `start_line/end_line` through executor dispatch.
5. Write-family content aliases reach handlers as canonical `content`; unknown content aliases are
   no longer silently dropped.
6. `execute_command` accepts argv/list/dict shapes only when they can be losslessly represented as a
   command string and still pass command safety policy.
7. `edit_blocks` intent recovery is either audit-only with clear receipts or enforce-mode with all
   Section 2.4 invariants passing.
8. Schema emission and runtime acceptance are checked by one parity test suite.
9. Handler-level alias fallback code has been removed or has a tracked exception with a test and
   coverage-gate waiver.
10. KernelOne release gate passes, or any remaining failure has a concrete owner, path, and reason.
