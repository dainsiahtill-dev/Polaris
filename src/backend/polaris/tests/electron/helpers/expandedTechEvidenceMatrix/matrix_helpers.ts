import { execFile } from "node:child_process";
import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { type Page, type TestInfo } from "@playwright/test";
import { CANDIDATE_RUNTIME_PROBE_IDS, CANDIDATE_SOURCE_PROBE_IDS, CORE_TECH_IDS, EXPANDED_TECH_CANDIDATES } from "./data";
import { type BackendConnection, type CandidateRuntimeCoverageRow, type CandidateRuntimeCoverageStatus, type CoreEvidenceSinkName, type CoreEvidenceSinkPlacement, type CoreRuntimeEvidencePlacement, type CoreRuntimeEvidencePlacementRow, type EvidenceProbe, type EvidenceRef, type EvidenceStatus, type ExpandedCandidateRuntimeCoverage, type ExpandedTechCandidate, type ExpandedTechEvidenceReport, type JsonRecord } from "./types";

function resolveRepoRoot(startDir: string): string {
  let current = path.resolve(startDir);
  while (true) {
    if (existsSync(path.join(current, "package.json")) && existsSync(path.join(current, "src", "backend"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error(`repository root not found from ${startDir}`);
    }
    current = parent;
  }
}

export const repoRoot = resolveRepoRoot(__dirname);
const execFileAsync = promisify(execFile);

export function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}

export function asRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : [];
}

export function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function parseJsonRecordFromCommandStdout(stdout: string): { payload: JsonRecord; error: string } {
  const text = stdout.trim();
  if (!text) {
    return { payload: {}, error: "stdout is empty" };
  }
  if (!text.startsWith("{")) {
    return { payload: {}, error: "stdout does not start with a JSON object" };
  }
  try {
    return { payload: asRecord(JSON.parse(text)), error: "" };
  } catch (error) {
    return { payload: {}, error: `stdout JSON parse failed: ${String(error)}` };
  }
}

export type RoleSessionKernelAuditMatch = {
  record: JsonRecord;
  rawEvent: JsonRecord;
  canonicalWrapped: boolean;
};

export function findRoleSessionKernelAuditEvent(
  records: JsonRecord[],
  sessionId: string,
): RoleSessionKernelAuditMatch | null {
  if (!sessionId) {
    return null;
  }
  const taskId = `role-session-${sessionId}`;
  for (const record of records.slice().reverse()) {
    const wrappedRaw = asRecord(record.raw);
    const rawEvent = Object.keys(wrappedRaw).length > 0 ? wrappedRaw : record;
    const task = asRecord(rawEvent.task);
    const data = asRecord(rawEvent.data);
    const refs = asRecord(record.refs);
    const matched =
      asString(task.task_id) === taskId ||
      asString(task.run_id) === taskId ||
      asString(data.session_id) === sessionId ||
      asString(refs.task_id) === taskId ||
      asString(refs.run_id) === taskId;
    if (
      matched &&
      asString(rawEvent.event_id) &&
      asString(rawEvent.prev_hash) &&
      asString(rawEvent.signature)
    ) {
      return {
        record,
        rawEvent,
        canonicalWrapped: rawEvent !== record,
      };
    }
  }
  return null;
}

export function isPathInsideOrSame(candidatePath: string, rootPath: string): boolean {
  if (!candidatePath || !rootPath) {
    return false;
  }
  const relative = path.relative(path.resolve(rootPath), path.resolve(candidatePath));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function countStatus(probes: EvidenceProbe[], status: EvidenceStatus): number {
  return probes.filter((probe) => probe.status === status).length;
}

export async function collectE2eAttachmentRuntimeProbe(testInfo: TestInfo, matrixFilename: string): Promise<EvidenceProbe> {
  const manifestPath = testInfo.outputPath("e2e-auto-attachment-manifest.json");
  const matrixPath = testInfo.outputPath(matrixFilename);
  const manifest = await readJsonIfExists<JsonRecord>(manifestPath);
  const entries = asRecords(asRecord(manifest).entries);
  const names = entries.map((entry) => asString(entry.name)).filter(Boolean);
  const hasProcessLogs = names.includes("web-backend-stdout") || names.includes("electron-main-stdout");
  const hasRendererLogs = names.includes("web-renderer-console") || names.includes("renderer-console");
  const outputDirExists = await pathExists(path.dirname(matrixPath));
  const pass = Boolean(
    asString(asRecord(manifest).schema) === "polaris.e2e.auto_attachment_manifest.v1" &&
      entries.length >= 2 &&
      hasProcessLogs &&
      hasRendererLogs &&
      outputDirExists,
  );

  return makeProbe({
    id: "e2e_attachment_runtime_probe",
    title: "E2E automatic evidence attachment runtime probe",
    category: "e2e",
    status: pass ? "PASS" : "WARN",
    required: false,
    evidence: [
      {
        type: "runtime_artifact",
        ref: manifestPath,
        value: {
          exists: Boolean(manifest),
          schema: asString(asRecord(manifest).schema),
          attachment_count: entries.length,
          attachment_names: names,
          has_process_logs: hasProcessLogs,
          has_renderer_logs: hasRendererLogs,
        },
      },
      {
        type: "runtime_artifact",
        ref: matrixPath,
        value: {
          planned_attachment_name: "expanded-tech-evidence-matrix",
          output_dir_exists: outputDirExists,
        },
      },
    ],
    findings: pass
      ? []
      : ["E2E automatic attachment manifest is missing process/renderer evidence entries for this run"],
  });
}

export function upsertProbe(probes: EvidenceProbe[], probe: EvidenceProbe): EvidenceProbe[] {
  return [...probes.filter((item) => item.id !== probe.id), probe];
}

export function refreshCandidateCoverageAndSummary(report: ExpandedTechEvidenceReport): void {
  report.candidate_runtime_coverage = buildExpandedCandidateRuntimeCoverage({
    candidates: EXPANDED_TECH_CANDIDATES,
    probes: report.probes,
    runtimeProbeCandidateIds: CANDIDATE_RUNTIME_PROBE_IDS,
    sourceProbeCandidateIds: CANDIDATE_SOURCE_PROBE_IDS,
  });
  report.summary = {
    pass: countStatus(report.probes, "PASS"),
    fail: countStatus(report.probes, "FAIL"),
    warn: countStatus(report.probes, "WARN"),
    skip: countStatus(report.probes, "SKIP"),
    required_fail: report.probes.filter((probe) => probe.required && probe.status === "FAIL").length,
    candidate_count: EXPANDED_TECH_CANDIDATES.length,
  };
}

export function makeProbe(input: EvidenceProbe): EvidenceProbe {
  return input;
}

function probeById(probes: EvidenceProbe[]): Map<string, EvidenceProbe> {
  return new Map(probes.map((probe) => [probe.id, probe]));
}

function passedCandidateProbeIds(
  candidateId: string,
  candidateProbeIds: Record<string, string[]>,
  probesById: Map<string, EvidenceProbe>,
): string[] {
  return Object.entries(candidateProbeIds)
    .filter(([probeId, candidateIds]) => candidateIds.includes(candidateId) && probesById.get(probeId)?.status === "PASS")
    .map(([probeId]) => probeId);
}

function evidenceForProbeIds(probeIds: string[], probesById: Map<string, EvidenceProbe>): EvidenceRef[] {
  return probeIds.flatMap((probeId) => probesById.get(probeId)?.evidence || []);
}

export function buildExpandedCandidateRuntimeCoverage(input: {
  candidates?: ExpandedTechCandidate[];
  probes: EvidenceProbe[];
  runtimeProbeCandidateIds?: Record<string, string[]>;
  sourceProbeCandidateIds?: Record<string, string[]>;
  runtimeRequiredStatuses?: ExpandedTechCandidate["status"][];
}): ExpandedCandidateRuntimeCoverage {
  const candidates = input.candidates || EXPANDED_TECH_CANDIDATES;
  const probesById = probeById(input.probes);
  const runtimeProbeCandidateIds = input.runtimeProbeCandidateIds || CANDIDATE_RUNTIME_PROBE_IDS;
  const sourceProbeCandidateIds = input.sourceProbeCandidateIds || CANDIDATE_SOURCE_PROBE_IDS;
  const runtimeRequiredStatuses = new Set(input.runtimeRequiredStatuses || ["implemented"]);

  const rows: CandidateRuntimeCoverageRow[] = candidates.map((candidate) => {
    const runtimeProbeIds = passedCandidateProbeIds(candidate.id, runtimeProbeCandidateIds, probesById);
    const sourceProbeIds = passedCandidateProbeIds(candidate.id, sourceProbeCandidateIds, probesById);
    const runtimeRequired = runtimeRequiredStatuses.has(candidate.status);
    let coverageStatus: CandidateRuntimeCoverageStatus = "declared_only";
    if (runtimeProbeIds.length > 0) {
      coverageStatus = "runtime_proved";
    } else if (sourceProbeIds.length > 0) {
      coverageStatus = "source_proved";
    } else if (candidate.status === "gate" && candidate.gates.length > 0) {
      coverageStatus = "gate_declared";
    }

    const evidenceProbeIds = [...runtimeProbeIds, ...sourceProbeIds];
    const findings: string[] = [];
    if (runtimeRequired && coverageStatus !== "runtime_proved") {
      findings.push("implemented candidate is not runtime-proved");
    }
    if (coverageStatus === "declared_only") {
      findings.push("candidate has no passing runtime/source/gate evidence in this matrix run");
    }

    return {
      candidate_id: candidate.id,
      title: candidate.title,
      category: candidate.category,
      declared_status: candidate.status,
      coverage_status: coverageStatus,
      runtime_required: runtimeRequired,
      evidence_probe_ids: evidenceProbeIds,
      evidence: evidenceForProbeIds(evidenceProbeIds, probesById),
      findings,
    };
  });

  const runtimeProvedRows = rows.filter((row) => row.coverage_status === "runtime_proved");
  const sourceProvedRows = rows.filter((row) => row.coverage_status === "source_proved");
  const gateDeclaredRows = rows.filter((row) => row.coverage_status === "gate_declared");
  const declaredOnlyRows = rows.filter((row) => row.coverage_status === "declared_only");
  const missingRuntimeRows = rows.filter((row) => row.runtime_required && row.coverage_status !== "runtime_proved");

  return {
    schema: "polaris.e2e.expanded_candidate_runtime_coverage.v1",
    expected_count: candidates.length,
    runtime_proved_count: runtimeProvedRows.length,
    source_proved_count: sourceProvedRows.length,
    gate_declared_count: gateDeclaredRows.length,
    declared_only_count: declaredOnlyRows.length,
    runtime_required_count: rows.filter((row) => row.runtime_required).length,
    missing_runtime_ids: missingRuntimeRows.map((row) => row.candidate_id),
    not_runtime_proved_ids: rows
      .filter((row) => row.coverage_status !== "runtime_proved")
      .map((row) => row.candidate_id),
    rows,
  };
}

export async function pathExists(filePath: string): Promise<boolean> {
  try {
    await fs.stat(filePath);
    return true;
  } catch {
    return false;
  }
}

export async function readTextIfExists(filePath: string): Promise<string | null> {
  try {
    return await fs.readFile(filePath, "utf-8");
  } catch {
    return null;
  }
}

export async function readJsonIfExists<T>(filePath: string): Promise<T | null> {
  const raw = await readTextIfExists(filePath);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function truncateForEvidence(value: unknown, maxChars = 4000): string {
  const text = String(value || "");
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, maxChars)}...<truncated ${text.length - maxChars} chars>`;
}

function commandOutputToString(value: string | Buffer | undefined): string {
  if (Buffer.isBuffer(value)) {
    return value.toString("utf-8");
  }
  return String(value || "");
}

export async function runUtf8CommandProbe(
  command: string,
  args: string[],
  options: { cwd: string; timeoutMs?: number; maxEvidenceChars?: number },
): Promise<{ exit_code: number | string; stdout: string; stderr: string; signal: string }> {
  try {
    const result = await execFileAsync(command, args, {
      cwd: options.cwd,
      timeout: options.timeoutMs || 30_000,
      env: {
        ...process.env,
        LC_ALL: "C.UTF-8",
        LANG: "C.UTF-8",
        PYTHONPATH: ".",
      },
      encoding: "utf-8",
      maxBuffer: 2 * 1024 * 1024,
    });
    return {
      exit_code: 0,
      stdout: truncateForEvidence(result.stdout, options.maxEvidenceChars),
      stderr: truncateForEvidence(result.stderr, options.maxEvidenceChars),
      signal: "",
    };
  } catch (error) {
    const commandError = error as {
      code?: number | string;
      signal?: string;
      stdout?: string | Buffer;
      stderr?: string | Buffer;
    };
    return {
      exit_code: commandError.code ?? 1,
      stdout: truncateForEvidence(commandOutputToString(commandError.stdout), options.maxEvidenceChars),
      stderr: truncateForEvidence(commandOutputToString(commandError.stderr), options.maxEvidenceChars),
      signal: String(commandError.signal || ""),
    };
  }
}

export async function writeUtf8File(filePath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content.endsWith("\n") ? content : `${content}\n`, "utf-8");
}

export async function listFilesByBasename(root: string, basenames: Set<string>, maxEntries = 4000): Promise<string[]> {
  if (!root || !(await pathExists(root))) {
    return [];
  }
  const matches: string[] = [];
  const stack = [root];
  let visited = 0;
  while (stack.length > 0 && visited < maxEntries) {
    const current = stack.pop();
    if (!current) {
      continue;
    }
    visited += 1;
    let entries: Awaited<ReturnType<typeof fs.readdir>>;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!["node_modules", ".git", ".venv", "__pycache__"].includes(entry.name)) {
          stack.push(fullPath);
        }
        continue;
      }
      if (basenames.has(entry.name)) {
        matches.push(fullPath);
      }
    }
  }
  return matches;
}

export async function newestFile(paths: string[]): Promise<string> {
  let selected = "";
  let selectedMtime = -1;
  for (const filePath of paths) {
    try {
      const stat = await fs.stat(filePath);
      if (stat.mtimeMs > selectedMtime) {
        selected = filePath;
        selectedMtime = stat.mtimeMs;
      }
    } catch {
      continue;
    }
  }
  return selected;
}

export async function readJsonlFiles(filePaths: string[], maxLinesPerFile = 2000): Promise<JsonRecord[]> {
  const records: JsonRecord[] = [];
  for (const filePath of filePaths) {
    const raw = await readTextIfExists(filePath);
    if (!raw) {
      continue;
    }
    const lines = raw.split(/\r?\n/).filter((line) => line.trim()).slice(-maxLinesPerFile);
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line) as unknown;
        const record = asRecord(parsed);
        if (Object.keys(record).length > 0) {
          records.push(record);
        }
      } catch {
        continue;
      }
    }
  }
  return records;
}

type JsonlFileEntry = {
  filePath: string;
  record: JsonRecord;
};

export async function listRuntimeAuditJsonlPaths(runtimeRootPath: string): Promise<string[]> {
  const auditDir = path.join(runtimeRootPath, "audit");
  try {
    const names = await fs.readdir(auditDir, { encoding: "utf-8" });
    return names
      .filter((name) => name.endsWith(".jsonl"))
      .sort()
      .map((name) => path.join(auditDir, name));
  } catch {
    return [];
  }
}

export async function readJsonlFileEntries(filePaths: string[], maxLinesPerFile = 2000): Promise<JsonlFileEntry[]> {
  const entries: JsonlFileEntry[] = [];
  for (const filePath of filePaths) {
    const raw = await readTextIfExists(filePath);
    if (!raw) {
      continue;
    }
    const lines = raw.split(/\r?\n/).filter((line) => line.trim()).slice(-maxLinesPerFile);
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line) as unknown;
        const record = asRecord(parsed);
        if (Object.keys(record).length > 0) {
          entries.push({ filePath, record });
        }
      } catch {
        continue;
      }
    }
  }
  return entries;
}

export function findRoleSessionKernelAuditEntry(
  entries: JsonlFileEntry[],
  sessionId: string,
): (RoleSessionKernelAuditMatch & { sourcePath: string }) | null {
  for (const entry of entries.slice().reverse()) {
    const match = findRoleSessionKernelAuditEvent([entry.record], sessionId);
    if (match) {
      return {
        ...match,
        sourcePath: entry.filePath,
      };
    }
  }
  return null;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).map((item) => item.trim()).filter(Boolean) : [];
}

export function yamlListItems(text: string, key: string): string[] {
  const values: string[] = [];
  const lines = text.split(/\r?\n/);
  let inBlock = false;
  let keyIndent = 0;
  const keyPrefix = `${key}:`;
  for (const line of lines) {
    const trimmed = line.trim();
    const indent = line.length - line.trimStart().length;
    if (!inBlock && trimmed.startsWith(keyPrefix)) {
      inBlock = true;
      keyIndent = indent;
      const sameLine = trimmed.slice(keyPrefix.length).trim();
      if (sameLine === "[]") {
        inBlock = false;
      }
      continue;
    }
    if (!inBlock) {
      continue;
    }
    if (trimmed === "") {
      continue;
    }
    if (indent <= keyIndent && !trimmed.startsWith("- ")) {
      inBlock = false;
      continue;
    }
    if (trimmed.startsWith("- ")) {
      const value = trimmed.slice(2).split("#")[0].trim().replace(/^['"]|['"]$/g, "");
      if (value) {
        values.push(value);
      }
    }
  }
  return values;
}

export function yamlScalar(text: string, key: string): string {
  const pattern = new RegExp(`^\\s*${key}:\\s*([^#\\n]+)`, "m");
  const match = pattern.exec(text);
  return match?.[1]?.trim().replace(/^['"]|['"]$/g, "") || "";
}

export function catalogCellIds(catalogText: string): string[] {
  const ids: string[] = [];
  const pattern = /^ {2}- id:\s*([^#\n]+)/gm;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(catalogText))) {
    const id = match[1].trim().replace(/^['"]|['"]$/g, "");
    if (id) {
      ids.push(id);
    }
  }
  return ids;
}

export function duplicateValues(values: string[]): JsonRecord[] {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return Array.from(counts.entries())
    .filter(([, count]) => count > 1)
    .map(([value, count]) => ({ value, count }));
}

export function isFixtureCellManifestPath(relativePath: string): boolean {
  return ["fixtures/", "sandbox/", "workspaces/"].some((pattern) => relativePath.includes(pattern));
}

function coreTechIdsFromReceipt(receipt: unknown): Set<string> {
  const receiptRecord = asRecord(receipt);
  const payload = asRecord(receiptRecord.payload);
  const ids = new Set<string>();
  for (const techId of stringArray(payload.core_tech_ids)) {
    ids.add(techId);
  }
  for (const row of asRecords(payload.rows)) {
    const techId = asString(row.tech_id);
    if (techId) {
      ids.add(techId);
    }
  }
  return ids;
}

function collectCoreTechIdsFromValue(value: unknown, coreTechIds: readonly string[]): Set<string> {
  const expected = new Set(coreTechIds);
  const ids = new Set<string>();
  const seen = new Set<object>();

  const visit = (candidate: unknown): void => {
    if (typeof candidate === "string") {
      if (expected.has(candidate)) {
        ids.add(candidate);
      }
      return;
    }
    if (!candidate || typeof candidate !== "object") {
      return;
    }
    if (seen.has(candidate)) {
      return;
    }
    seen.add(candidate);

    if (Array.isArray(candidate)) {
      for (const item of candidate) {
        visit(item);
      }
      return;
    }

    for (const item of Object.values(candidate as JsonRecord)) {
      visit(item);
    }
  };

  visit(value);
  return ids;
}

function coreTechIdsFromEvidenceRefs(refs: EvidenceRef[], coreTechIds: readonly string[]): Set<string> {
  const ids = new Set<string>();
  for (const ref of refs) {
    for (const techId of collectCoreTechIdsFromValue(ref, coreTechIds)) {
      ids.add(techId);
    }
  }
  return ids;
}

function evidenceRefsForTechId(refs: EvidenceRef[], techId: string, coreTechIds: readonly string[]): EvidenceRef[] {
  return refs.filter((ref) => collectCoreTechIdsFromValue(ref, coreTechIds).has(techId));
}

export function taskProjectionSummary(taskProjection: unknown): CoreRuntimeEvidencePlacement["task_projection"] {
  const taskProjectionRecord = asRecord(taskProjection);
  const rawTasks = Array.isArray(taskProjectionRecord.tasks) ? taskProjectionRecord.tasks : [];
  const tasks = rawTasks.map(asRecord);
  const linkedPmTaskCount = tasks.filter((task) => {
    const metadata = asRecord(task.metadata);
    return Boolean(asString(task.pm_task_id) || asString(metadata.pm_task_id));
  }).length;
  const projectionSources = new Set<string>();
  for (const task of tasks) {
    const metadata = asRecord(task.metadata);
    const source = asString(metadata.projection_source || task.projection_source);
    if (source) {
      projectionSources.add(source);
    }
  }
  return {
    task_count: tasks.length,
    linked_pm_task_count: linkedPmTaskCount,
    projection_source_count: projectionSources.size,
  };
}

function makeSink(
  present: boolean,
  evidence: EvidenceRef[],
  missingFinding: string,
): CoreEvidenceSinkPlacement {
  return {
    present,
    evidence: present ? evidence : [],
    findings: present ? [] : [missingFinding],
  };
}

export function buildCoreRuntimeEvidencePlacement(input: {
  coreTechIds?: readonly string[];
  auditRefs: EvidenceRef[];
  receipt: unknown;
  handoff: unknown;
  taskProjection: unknown;
}): CoreRuntimeEvidencePlacement {
  const expectedSinks: CoreEvidenceSinkName[] = ["audit", "receipt", "handoff", "task_projection"];
  const coreTechIds = Array.from(new Set((input.coreTechIds || CORE_TECH_IDS).map(String).filter(Boolean)));
  const receiptRecord = asRecord(input.receipt);
  const handoffRecord = asRecord(input.handoff);
  const receiptId = asString(receiptRecord.receipt_id || receiptRecord.id);
  const handoffId = asString(handoffRecord.handoff_id || handoffRecord.id);
  const receiptCoreIds = coreTechIdsFromReceipt(receiptRecord);
  const auditCoreIds = coreTechIdsFromEvidenceRefs(input.auditRefs, coreTechIds);
  const handoffReceiptRefs = new Set([
    ...stringArray(handoffRecord.receipt_refs),
    ...stringArray(asRecord(handoffRecord.turn_envelope).receipt_ids),
  ]);
  const handoffLinksReceipt = Boolean(receiptId && handoffReceiptRefs.has(receiptId));
  const taskSummary = taskProjectionSummary(input.taskProjection);
  const taskProjectionCoreIds = collectCoreTechIdsFromValue(input.taskProjection, coreTechIds);
  const taskProjectionPresent = taskSummary.task_count > 0 && taskSummary.linked_pm_task_count > 0;
  const receiptEvidence: EvidenceRef[] = receiptId
    ? [{ type: "api", ref: `/cognitive-runtime/runtime-receipts/${receiptId}`, value: { receipt_id: receiptId } }]
    : [];
  const handoffEvidence: EvidenceRef[] = handoffId
    ? [{ type: "api", ref: `/cognitive-runtime/handoffs/${handoffId}`, value: { handoff_id: handoffId, receipt_id: receiptId } }]
    : [];
  const taskProjectionEvidence: EvidenceRef[] = [
    { type: "api", ref: "/v2/director/tasks?source=auto", value: taskSummary },
  ];
  const rows: CoreRuntimeEvidencePlacementRow[] = [];
  const missing: string[] = [];

  for (const techId of coreTechIds) {
    const auditEvidence = evidenceRefsForTechId(input.auditRefs, techId, coreTechIds);
    const sinks: Record<CoreEvidenceSinkName, CoreEvidenceSinkPlacement> = {
      audit: makeSink(
        auditCoreIds.has(techId),
        auditEvidence,
        "core technology id is missing from audit artifacts",
      ),
      receipt: makeSink(
        receiptCoreIds.has(techId),
        receiptEvidence,
        "core technology id is missing from runtime receipt payload",
      ),
      handoff: makeSink(
        handoffLinksReceipt && receiptCoreIds.has(techId),
        handoffEvidence,
        "handoff does not reference the receipt carrying this core technology id",
      ),
      task_projection: makeSink(
        taskProjectionPresent && taskProjectionCoreIds.has(techId),
        taskProjectionEvidence,
        "core technology id is missing from Director task projection rows",
      ),
    };
    for (const sinkName of expectedSinks) {
      if (!sinks[sinkName].present) {
        missing.push(`${techId}:${sinkName}`);
      }
    }
    rows.push({ tech_id: techId, sinks });
  }

  return {
    schema: "polaris.e2e.core_runtime_evidence_placement.v1",
    expected_sinks: expectedSinks,
    rows,
    missing,
    receipt_id: receiptId,
    handoff_id: handoffId,
    task_projection: taskSummary,
  };
}

export async function candidateSourceProbe(
  id: string,
  title: string,
  category: string,
  candidateIds: string[],
  requiredPathCount = 1,
): Promise<EvidenceProbe> {
  const candidates = EXPANDED_TECH_CANDIDATES.filter((candidate) => candidateIds.includes(candidate.id));
  const uniquePaths = Array.from(new Set(candidates.flatMap((candidate) => candidate.paths)));
  const existingPaths: string[] = [];
  for (const relPath of uniquePaths) {
    const fullPath = path.join(repoRoot, relPath);
    if (await pathExists(fullPath)) {
      existingPaths.push(relPath);
    }
  }
  const status = existingPaths.length >= requiredPathCount ? "PASS" : "FAIL";
  return makeProbe({
    id,
    title,
    category,
    status,
    required: true,
    evidence: [
      {
        type: "repo_path",
        ref: repoRoot,
        value: { expected_any_of: uniquePaths, existing: existingPaths },
      },
    ],
    findings: status === "PASS" ? [] : [`expected at least ${requiredPathCount} source paths to exist`],
  });
}

export async function getBackendInfoFromPage(page: Page): Promise<BackendConnection> {
  const snapshot = await page.evaluate(async () => {
    type BrowserBackendInfo = { baseUrl?: string | null; token?: string | null };
    type BrowserWindow = Window & {
      polaris?: { getBackendInfo?: () => Promise<BrowserBackendInfo> };
      __DEV_BACKEND__?: BrowserBackendInfo;
    };
    const currentWindow = window as BrowserWindow;
    if (currentWindow.polaris?.getBackendInfo) {
      const preloadInfo = await currentWindow.polaris.getBackendInfo();
      return { preloadInfo };
    }
    const devBackend = currentWindow.__DEV_BACKEND__;
    return {
      devBackend,
      storedBaseUrl: window.localStorage.getItem("polaris.baseUrl"),
      storedToken: window.localStorage.getItem("polaris.token"),
    };
  });

  return resolveBackendInfoSnapshot(snapshot);
}

export function resolveBackendInfoSnapshot(snapshot: unknown): BackendConnection {
  const record = asRecord(snapshot);
  const preloadInfo = asRecord(record.preloadInfo);
  const devBackend = asRecord(record.devBackend);
  const storedBaseUrl = asString(record.storedBaseUrl);
  const baseUrl = asString(preloadInfo.baseUrl || devBackend.baseUrl || storedBaseUrl || "http://127.0.0.1:49977").replace(/\/+$/, "");
  const token = asString(preloadInfo.token || devBackend.token || record.storedToken || "");
  const source = asString(preloadInfo.baseUrl)
    ? "electron_preload"
    : asString(devBackend.baseUrl)
      ? "browser_dev_backend"
      : storedBaseUrl
        ? "browser_local_storage"
        : "default_loopback";
  if (!baseUrl) {
    throw new Error("backend baseUrl missing");
  }
  return { baseUrl, token, source };
}

export async function requestJson<T>(
  page: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: JsonRecord; timeoutMs?: number },
): Promise<T> {
  const backend = await getBackendInfoFromPage(page);
  return page.evaluate(
    async ({ baseUrl, token, apiPath, method, body, timeoutMs }) => {
      const headers: Record<string, string> = {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      };
      if (token) {
        headers.authorization = `Bearer ${token}`;
      }
      if (body) {
        headers["Content-Type"] = "application/json";
      }
      const controller = new AbortController();
      const timer =
        Number(timeoutMs) > 0 ? window.setTimeout(() => controller.abort(), Number(timeoutMs)) : undefined;
      try {
        const response = await fetch(`${baseUrl}${apiPath}`, {
          method,
          cache: "no-store",
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail = await response.text().catch(() => "");
          throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
        }
        return (await response.json()) as unknown;
      } catch (error) {
        const name = error instanceof Error ? error.name : "";
        if (name === "AbortError") {
          throw new Error(`fetch ${apiPath} timed out after ${timeoutMs}ms`);
        }
        throw error;
      } finally {
        if (timer !== undefined) {
          window.clearTimeout(timer);
        }
      }
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
      timeoutMs: options?.timeoutMs || 20_000,
    },
  ) as Promise<T>;
}

export async function requestText(
  page: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: JsonRecord },
): Promise<string> {
  const backend = await getBackendInfoFromPage(page);
  return page.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const headers: Record<string, string> = {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      };
      if (token) {
        headers.authorization = `Bearer ${token}`;
      }
      if (body) {
        headers["Content-Type"] = "application/json";
      }
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
      }
      return await response.text();
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
    },
  ) as Promise<string>;
}

type RuntimeWebSocketExercise = {
  opened: boolean;
  closed: boolean;
  closeCode: number | null;
  closeReason: string;
  statusReceived: boolean;
  statusType: string;
  messageTypes: string[];
  error: string;
};

async function exerciseRuntimeWebSocket(
  wsUrl: string,
  sendStatusRequest: boolean,
): Promise<RuntimeWebSocketExercise> {
  return await new Promise<RuntimeWebSocketExercise>((resolve) => {
    const result: RuntimeWebSocketExercise = {
      opened: false,
      closed: false,
      closeCode: null,
      closeReason: "",
      statusReceived: false,
      statusType: "",
      messageTypes: [],
      error: "",
    };
    let settled = false;
    const socket = new WebSocket(wsUrl);
    const timeout = setTimeout(() => {
      result.error = result.error || "timeout";
      finish();
    }, 8_000);

    const finish = () => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      try {
        socket.close();
      } catch {
        // Ignore close races; the observed result above is the evidence.
      }
      resolve(result);
    };

    socket.addEventListener("open", () => {
      result.opened = true;
      if (sendStatusRequest) {
        socket.send(JSON.stringify({ type: "GET_STATUS", roles: ["pm", "director", "qa"] }));
      }
    });
    socket.addEventListener("message", async (event) => {
      try {
        const raw =
          typeof event.data === "string"
            ? event.data
            : event.data instanceof ArrayBuffer
              ? Buffer.from(event.data).toString("utf-8")
              : typeof (event.data as { text?: () => Promise<string> }).text === "function"
                ? await (event.data as { text: () => Promise<string> }).text()
                : String(event.data || "");
        const payload = JSON.parse(raw) as { type?: string };
        const type = String(payload.type || "");
        if (type) {
          result.messageTypes.push(type);
        }
        if (sendStatusRequest && type === "status") {
          result.statusReceived = true;
          result.statusType = type;
          finish();
        }
      } catch {
        result.error = result.error || "invalid_json_message";
      }
    });
    socket.addEventListener("error", () => {
      result.error = result.error || "websocket_error";
    });
    socket.addEventListener("close", (event) => {
      result.closed = true;
      result.closeCode = event.code;
      result.closeReason = event.reason || "";
      finish();
    });
  });
}

function runtimeWebSocketUrl(backend: BackendConnection, token: string, workspace: string): string {
  const params = new URLSearchParams();
  params.set("token", token);
  if (workspace) {
    params.set("workspace", workspace);
  }
  return `${backend.baseUrl.replace(/^http/i, "ws")}/v2/ws/runtime?${params.toString()}`;
}

export async function collectWebSocketStaleTokenRuntimeProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  try {
    const backend = await getBackendInfoFromPage(page);
    const staleToken = `stale-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const staleResult = await exerciseRuntimeWebSocket(
      runtimeWebSocketUrl(backend, staleToken, workspace),
      false,
    );
    const freshResult = await exerciseRuntimeWebSocket(
      runtimeWebSocketUrl(backend, backend.token, workspace),
      true,
    );
    const staleRejected = staleResult.closeCode === 1008 && !staleResult.statusReceived;
    const freshRecovered = freshResult.opened && freshResult.statusReceived && freshResult.statusType === "status";
    const pass = Boolean(backend.token && staleRejected && freshRecovered);

    return makeProbe({
      id: "websocket_stale_token_runtime_probe",
      title: "WebSocket stale-token rejection and fresh-token recovery runtime probe",
      category: "runtime",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "probe",
          ref: "/v2/ws/runtime?token=<stale>",
          value: {
            opened: staleResult.opened,
            closed: staleResult.closed,
            close_code: staleResult.closeCode,
            status_received: staleResult.statusReceived,
            error: staleResult.error,
          },
        },
        {
          type: "probe",
          ref: "/v2/ws/runtime?token=<fresh>",
          value: {
            backend_source: backend.source,
            token_present: Boolean(backend.token),
            opened: freshResult.opened,
            closed: freshResult.closed,
            status_received: freshResult.statusReceived,
            status_type: freshResult.statusType,
            message_types: freshResult.messageTypes,
            error: freshResult.error,
          },
        },
      ],
      findings: pass
        ? []
        : ["runtime WebSocket did not reject stale token and recover with the current backend token"],
    });
  } catch (error) {
    return makeProbe({
      id: "websocket_stale_token_runtime_probe",
      title: "WebSocket stale-token rejection and fresh-token recovery runtime probe",
      category: "runtime",
      status: "WARN",
      required: false,
      evidence: [
        { type: "probe", ref: "/v2/ws/runtime?token=<stale>" },
        { type: "probe", ref: "/v2/ws/runtime?token=<fresh>" },
      ],
      findings: [String(error)],
    });
  }
}
