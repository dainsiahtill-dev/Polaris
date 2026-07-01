import { type Page, type TestInfo, test as base } from "@playwright/test";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";

export type WebTestEnvironment = {
  isolatedE2EHome: string;
  isolatedRuntimeRoot: string;
  isolatedWorkspace: string;
  settingsHome: string;
  useRealSettings: boolean;
};

export type WebBackendInfo = {
  baseUrl: string;
  token: string;
  frontendUrl: string;
};

type StaticServerHandle = {
  url: string;
  close: () => Promise<void>;
};

type WebFixtures = {
  webTestEnv: WebTestEnvironment;
  webBackendInfo: WebBackendInfo;
  webPage: Page;
};

type AutoAttachmentManifestEntry = {
  name: string;
  filename: string;
  content_type: string;
  fixture: string;
  phase: "setup" | "finalizer";
};

function resolveRepoRoot(startDir: string): string {
  let current = path.resolve(startDir);
  while (true) {
    if (fs.existsSync(path.join(current, "package.json")) && fs.existsSync(path.join(current, "src", "backend"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error(`[web-fixtures] repository root not found from ${startDir}`);
    }
    current = parent;
  }
}

const repoRoot = resolveRepoRoot(__dirname);
const e2eHomeRoot = path.join(os.tmpdir(), "Polaris", "web-e2e-home");
const runtimeBase = path.join(os.tmpdir(), "Polaris", "runtime", "web-e2e");
const workspaceBase = path.join(os.tmpdir(), "Polaris", "web-e2e-workspace");

function isPathInside(basePath: string, candidatePath: string): boolean {
  const base = path.resolve(basePath);
  const candidate = path.resolve(candidatePath);
  if (candidate === base) {
    return true;
  }
  const relative = path.relative(base, candidate);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function assertOutsideRepo(candidatePath: string, label: string): string {
  const resolved = path.resolve(candidatePath);
  if (isPathInside(repoRoot, resolved)) {
    throw new Error(`[web-fixtures] ${label} must not be inside the Polaris meta-project repository.`);
  }
  return resolved;
}

function createTempDir(root: string, prefix: string, label: string): string {
  fs.mkdirSync(root, { recursive: true });
  return assertOutsideRepo(fs.mkdtempSync(path.join(root, prefix)), label);
}

function createWorkspace(): string {
  const workspace = createTempDir(workspaceBase, "workspace-", "KERNELONE_WORKSPACE");
  const productDocs = path.join(workspace, "docs", "product");
  fs.mkdirSync(productDocs, { recursive: true });
  const requirements = [
    "# Web E2E Workflow Audit Service",
    "",
    "Build a Node.js + TypeScript service for task workflow auditing.",
    "",
    "## Required Modules",
    "",
    "- `src/models/task.ts`: task state model with `PENDING`, `RUNNING`, and `DONE` states.",
    "- `src/services/taskGraph.ts`: validate dependency graphs and reject circular dependencies.",
    "- `src/services/auditLog.ts`: append immutable audit entries when a task status changes.",
    "- `src/server/app.ts`: expose a small HTTP-style application entry for integration tests.",
    "- `tests/unit/taskGraph.test.ts`: unit coverage for graph validation.",
    "- `tests/integration/auditFlow.test.ts`: integration coverage for task transition audit records.",
    "- `package.json`, `tsconfig.json`, and a test script must be present.",
    "",
    "## Acceptance Criteria",
    "",
    "- At least three source modules are created under `src/`.",
    "- A task transition from `PENDING` to `RUNNING` creates an audit record with a stable `audit_id`.",
    "- A dependency cycle such as A -> B and B -> A is rejected with a deterministic error code.",
    "- Unit and integration tests are present and can be invoked through the package test script.",
    "- Use UTF-8 text files only.",
    "",
    "## Constraints",
    "",
    "- Use TypeScript/JavaScript only.",
    "- Do not create Python, Go, Rust, or shell application entry points.",
    "- Do not create `src/main.py`, `src/app.py`, `requirements.txt`, `pyproject.toml`, or `setup.py`.",
  ].join("\n");
  const plan = [
    "# Web E2E Implementation Plan",
    "",
    "1. Create project configuration: `package.json`, `tsconfig.json`, and test script.",
    "2. Implement task model and task graph validation modules.",
    "3. Implement immutable audit logging for task state transitions.",
    "4. Add HTTP-style app composition in `src/server/app.ts`.",
    "5. Add unit and integration tests covering graph cycles and audit transitions.",
  ].join("\n");
  fs.mkdirSync(path.join(workspace, "docs"), { recursive: true });
  fs.writeFileSync(
    path.join(workspace, "docs", "README.md"),
    `${requirements}\n`,
    { encoding: "utf8" },
  );
  fs.writeFileSync(path.join(productDocs, "requirements.md"), `${requirements}\n`, { encoding: "utf8" });
  fs.writeFileSync(path.join(productDocs, "plan.md"), `${plan}\n`, { encoding: "utf8" });
  return workspace;
}

function cleanupPath(target: string): void {
  if (!target) {
    return;
  }
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // Ignore cleanup failures.
  }
}

function recordAutoAttachmentManifest(testInfo: TestInfo, entries: AutoAttachmentManifestEntry[]): void {
  const manifestPath = testInfo.outputPath("e2e-auto-attachment-manifest.json");
  let existingEntries: AutoAttachmentManifestEntry[] = [];
  if (fs.existsSync(manifestPath)) {
    try {
      const parsed = JSON.parse(fs.readFileSync(manifestPath, { encoding: "utf-8" })) as {
        entries?: AutoAttachmentManifestEntry[];
      };
      existingEntries = Array.isArray(parsed.entries) ? parsed.entries : [];
    } catch {
      existingEntries = [];
    }
  }
  const merged = new Map<string, AutoAttachmentManifestEntry>();
  for (const entry of [...existingEntries, ...entries]) {
    merged.set(`${entry.fixture}:${entry.name}:${entry.filename}`, entry);
  }
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify(
      {
        schema: "polaris.e2e.auto_attachment_manifest.v1",
        generated_at: new Date().toISOString(),
        entries: Array.from(merged.values()).sort((left, right) => left.name.localeCompare(right.name)),
      },
      null,
      2,
    )}\n`,
    { encoding: "utf-8" },
  );
}

async function isPortAvailable(port: number): Promise<boolean> {
  return await new Promise<boolean>((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen({ host: "127.0.0.1", port, exclusive: true });
  });
}

async function selectPort(startPort: number): Promise<number> {
  for (let offset = 0; offset < 50; offset += 1) {
    const candidate = startPort + offset;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortAvailable(candidate)) {
      return candidate;
    }
  }
  throw new Error(`[web-fixtures] no free port near ${startPort}`);
}

function resolvePython(): string {
  const configured = String(process.env.KERNELONE_PYTHON || "").trim();
  if (configured && fs.existsSync(configured)) {
    return configured;
  }
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  return fs.existsSync(venvPython) ? venvPython : "python3";
}

function resolvePythonPath(baseEnv: NodeJS.ProcessEnv): string {
  const backendSource = path.join(repoRoot, "src", "backend");
  const python = resolvePython();
  const sitePackages = python.startsWith(path.join(repoRoot, ".venv"))
    ? fs.readdirSync(path.join(repoRoot, ".venv", "lib"), { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("python"))
      .map((entry) => path.join(repoRoot, ".venv", "lib", entry.name, "site-packages"))
      .find((candidate) => fs.existsSync(candidate))
    : "";
  return [sitePackages || "", backendSource, String(baseEnv.PYTHONPATH || "")]
    .filter(Boolean)
    .join(path.delimiter);
}

function mimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js" || ext === ".mjs") return "application/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".json") return "application/json; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".woff") return "font/woff";
  if (ext === ".woff2") return "font/woff2";
  return "application/octet-stream";
}

async function startStaticServer(distDir: string): Promise<StaticServerHandle> {
  const distRoot = path.resolve(distDir);
  if (!fs.existsSync(path.join(distRoot, "index.html"))) {
    throw new Error(`[web-fixtures] renderer dist missing: ${distRoot}`);
  }
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
    const normalizedPath = path.normalize(requestUrl.pathname === "/" ? "index.html" : requestUrl.pathname).replace(/^[/\\]+/, "");
    let target = path.resolve(distRoot, normalizedPath);
    if (!target.startsWith(distRoot)) {
      response.writeHead(403);
      response.end("Forbidden");
      return;
    }
    if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
      target = path.join(distRoot, "index.html");
    }
    response.writeHead(200, { "Content-Type": mimeType(target) });
    fs.createReadStream(target).pipe(response);
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("[web-fixtures] static server did not expose a TCP port");
  }
  return {
    url: `http://127.0.0.1:${address.port}/index.html`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

async function waitForBackend(baseUrl: string, token: string): Promise<void> {
  const deadline = Date.now() + 90_000;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/settings`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        return;
      }
      lastError = `${response.status} ${await response.text().catch(() => "")}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`[web-fixtures] backend readiness timeout: ${lastError}`);
}

function stopProcess(child: ChildProcessWithoutNullStreams | null): void {
  if (!child || child.killed) {
    return;
  }
  child.kill("SIGTERM");
  setTimeout(() => {
    if (!child.killed) {
      child.kill("SIGKILL");
    }
  }, 5000).unref();
}

export const test = base.extend<WebFixtures>({
  webTestEnv: async ({}, use) => {
    const isolatedE2EHome = createTempDir(e2eHomeRoot, "home-", "KERNELONE_HOME");
    const isolatedRuntimeRoot = createTempDir(runtimeBase, "runtime-", "KERNELONE_RUNTIME_ROOT");
    const isolatedWorkspace = createWorkspace();
    const useRealSettings = process.env.KERNELONE_E2E_USE_REAL_SETTINGS === "1";
    const realSettingsHome = String(process.env.KERNELONE_E2E_HOME || process.env.KERNELONE_HOME || "").trim();
    const settingsHome = useRealSettings
      ? realSettingsHome
        ? path.resolve(realSettingsHome)
        : path.join(os.homedir(), ".polaris")
      : isolatedE2EHome;

    try {
      await use({ isolatedE2EHome, isolatedRuntimeRoot, isolatedWorkspace, settingsHome, useRealSettings });
    } finally {
      cleanupPath(isolatedE2EHome);
      cleanupPath(isolatedRuntimeRoot);
      cleanupPath(isolatedWorkspace);
    }
  },

  webBackendInfo: async ({ webTestEnv }, use, testInfo) => {
    recordAutoAttachmentManifest(testInfo, [
      {
        name: "web-backend-stdout",
        filename: "web-backend-stdout.log",
        content_type: "text/plain",
        fixture: "webBackendInfo",
        phase: "finalizer",
      },
      {
        name: "web-backend-stderr",
        filename: "web-backend-stderr.log",
        content_type: "text/plain",
        fixture: "webBackendInfo",
        phase: "finalizer",
      },
    ]);
    const backendPort = await selectPort(Number.parseInt(process.env.KERNELONE_BACKEND_PORT || "49977", 10));
    const token = String(process.env.KERNELONE_BACKEND_TOKEN || "").trim() || crypto.randomBytes(16).toString("hex");
    const baseUrl = `http://127.0.0.1:${backendPort}`;
    const distDir = path.join(repoRoot, "src", "frontend", "dist");
    const staticServer = await startStaticServer(distDir);
    const workspace = process.env.KERNELONE_E2E_ALLOW_REAL_WORKSPACE_MUTATION === "1" && process.env.KERNELONE_WORKSPACE
      ? assertOutsideRepo(process.env.KERNELONE_WORKSPACE, "KERNELONE_WORKSPACE")
      : webTestEnv.isolatedWorkspace;
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONPATH: resolvePythonPath(process.env),
      KERNELONE_HOME: webTestEnv.settingsHome,
      KERNELONE_RUNTIME_ROOT: webTestEnv.isolatedRuntimeRoot,
      KERNELONE_WORKSPACE: workspace,
      KERNELONE_STATE_TO_RAMDISK: "0",
      KERNELONE_E2E: "1",
      KERNELONE_NATS_ENABLED: process.env.KERNELONE_NATS_ENABLED || "1",
      KERNELONE_NATS_REQUIRED: process.env.KERNELONE_NATS_REQUIRED || "1",
      KERNELONE_BACKEND_PORT: String(backendPort),
      KERNELONE_BACKEND_TOKEN: token,
      KERNELONE_CORS_ORIGINS: [
        staticServer.url ? new URL(staticServer.url).origin : "",
        `http://127.0.0.1:${backendPort}`,
      ].filter(Boolean).join(","),
    };
    const backend = spawn(resolvePython(), [
      "-m",
      "polaris.delivery.server",
      "--host",
      "127.0.0.1",
      "--port",
      String(backendPort),
      "--token",
      token,
      "--workspace",
      workspace,
      "--cors-origins",
      env.KERNELONE_CORS_ORIGINS || "",
    ], {
      cwd: repoRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout: string[] = [];
    const stderr: string[] = [];
    backend.stdout.on("data", (chunk) => stdout.push(String(chunk)));
    backend.stderr.on("data", (chunk) => stderr.push(String(chunk)));

    try {
      await waitForBackend(baseUrl, token);
      await use({ baseUrl, token, frontendUrl: staticServer.url });
    } finally {
      const stdoutPath = testInfo.outputPath("web-backend-stdout.log");
      const stderrPath = testInfo.outputPath("web-backend-stderr.log");
      fs.writeFileSync(stdoutPath, stdout.join(""), { encoding: "utf8" });
      fs.writeFileSync(stderrPath, stderr.join(""), { encoding: "utf8" });
      await testInfo.attach("web-backend-stdout", { path: stdoutPath, contentType: "text/plain" });
      await testInfo.attach("web-backend-stderr", { path: stderrPath, contentType: "text/plain" });
      stopProcess(backend);
      await staticServer.close();
    }
  },

  webPage: async ({ page, webBackendInfo }, use, testInfo) => {
    recordAutoAttachmentManifest(testInfo, [
      {
        name: "web-renderer-console",
        filename: "web-renderer-console.jsonl",
        content_type: "application/jsonlines",
        fixture: "webPage",
        phase: "finalizer",
      },
      {
        name: "web-renderer-pageerror",
        filename: "web-renderer-pageerror.jsonl",
        content_type: "application/jsonlines",
        fixture: "webPage",
        phase: "finalizer",
      },
    ]);
    const consoleLines: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", (message) => consoleLines.push(JSON.stringify({ type: message.type(), text: message.text() })));
    page.on("pageerror", (error) => pageErrors.push(JSON.stringify({ name: error.name, message: error.message, stack: error.stack || "" })));
    await page.addInitScript(({ baseUrl, token }) => {
      const targetWindow = window as Window & {
        __DEV_BACKEND__?: { baseUrl: string; token: string };
      };
      targetWindow.__DEV_BACKEND__ = { baseUrl, token };
      window.localStorage.setItem("polaris.baseUrl", baseUrl);
      window.localStorage.setItem("polaris.token", token);
    }, {
      baseUrl: webBackendInfo.baseUrl,
      token: webBackendInfo.token,
    });
    await page.goto(webBackendInfo.frontendUrl, { waitUntil: "domcontentloaded" });
    try {
      await use(page);
    } finally {
      const consolePath = testInfo.outputPath("web-renderer-console.jsonl");
      const errorsPath = testInfo.outputPath("web-renderer-pageerror.jsonl");
      fs.writeFileSync(consolePath, `${consoleLines.join("\n")}\n`, { encoding: "utf8" });
      fs.writeFileSync(errorsPath, `${pageErrors.join("\n")}\n`, { encoding: "utf8" });
      await testInfo.attach("web-renderer-console", { path: consolePath, contentType: "application/jsonlines" });
      await testInfo.attach("web-renderer-pageerror", { path: errorsPath, contentType: "application/jsonlines" });
    }
  },
});

export { expect } from "@playwright/test";
