import { _electron as electron, type ElectronApplication, type Page, test as base } from "@playwright/test";
import fs from "fs";
import http from "http";
import path from "path";

export type MainProcessLogs = {
  stdout: string[];
  stderr: string[];
};

export type TestEnvironment = {
  isolatedE2EHome: string;
  isolatedRuntimeRoot: string;
  useRealSettings: boolean;
};

type Fixtures = {
  mainProcessLogs: MainProcessLogs;
  testEnv: TestEnvironment;
  electronApp: ElectronApplication;
  window: Page;
};

const repoRoot = path.resolve(__dirname, "..", "..");
const e2eHomeRoot = path.join(repoRoot, ".polaris", "tmp");

function createIsolatedE2EHome(): string {
  fs.mkdirSync(e2eHomeRoot, { recursive: true });
  return fs.mkdtempSync(path.join(e2eHomeRoot, "electron-e2e-home-"));
}

function createIsolatedRuntimeRoot(isolatedE2EHome: string): string {
  const runtimeRoot = path.join(isolatedE2EHome, "runtime-cache");
  fs.mkdirSync(runtimeRoot, { recursive: true });
  return runtimeRoot;
}

function cleanupIsolatedE2EHome(target: string): void {
  if (!target) {
    return;
  }
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // ignore cleanup failures
  }
}

function resolveFirstExistingPath(candidates: string[], label: string): string {
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error(`[fixtures] ${label} not found. Checked: ${candidates.join(", ")}`);
}

const electronMain = resolveFirstExistingPath(
  [
    path.join(repoRoot, "src", "electron", "main.cjs"),
    path.join(repoRoot, "electron", "main.cjs"),
  ],
  "electron main entry",
);

function resolveVenvPython(): string {
  const venvRoot = path.join(repoRoot, ".venv");
  const pythonPath = process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
  return fs.existsSync(pythonPath) ? pythonPath : "";
}

type StaticDevServerHandle = {
  url: string;
  close: () => Promise<void>;
};

function resolveMimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case ".html":
      return "text/html; charset=utf-8";
    case ".js":
    case ".mjs":
      return "application/javascript; charset=utf-8";
    case ".css":
      return "text/css; charset=utf-8";
    case ".json":
      return "application/json; charset=utf-8";
    case ".svg":
      return "image/svg+xml";
    case ".png":
      return "image/png";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".gif":
      return "image/gif";
    case ".webp":
      return "image/webp";
    case ".woff":
      return "font/woff";
    case ".woff2":
      return "font/woff2";
    case ".ttf":
      return "font/ttf";
    default:
      return "application/octet-stream";
  }
}

async function startStaticDevServer(distDir: string): Promise<StaticDevServerHandle> {
  const distRoot = path.resolve(distDir);
  const server = http.createServer((request, response) => {
    try {
      const method = String(request.method || "GET").toUpperCase();
      if (method !== "GET" && method !== "HEAD") {
        response.statusCode = 405;
        response.end("Method Not Allowed");
        return;
      }

      const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
      let pathname = decodeURIComponent(requestUrl.pathname || "/");
      if (pathname === "/") {
        pathname = "/index.html";
      }

      const normalizedPath = path.normalize(pathname).replace(/^[/\\]+/, "");
      let targetFile = path.resolve(distRoot, normalizedPath);
      if (!targetFile.startsWith(distRoot)) {
        response.statusCode = 403;
        response.end("Forbidden");
        return;
      }

      let stat: fs.Stats | null = null;
      try {
        stat = fs.statSync(targetFile);
      } catch {
        stat = null;
      }

      // SPA route fallback for extensionless paths.
      if ((!stat || !stat.isFile()) && !path.extname(normalizedPath)) {
        targetFile = path.join(distRoot, "index.html");
        if (fs.existsSync(targetFile)) {
          stat = fs.statSync(targetFile);
        }
      }

      if (!stat || !stat.isFile()) {
        response.statusCode = 404;
        response.end("Not Found");
        return;
      }

      response.statusCode = 200;
      response.setHeader("Content-Type", resolveMimeType(targetFile));
      if (method === "HEAD") {
        response.end();
        return;
      }

      const stream = fs.createReadStream(targetFile);
      stream.on("error", () => {
        response.statusCode = 500;
        response.end("Internal Server Error");
      });
      stream.pipe(response);
    } catch (error) {
      response.statusCode = 500;
      response.end(`Internal Server Error: ${String(error)}`);
    }
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    throw new Error("Failed to start static dev server.");
  }

  const url = `http://127.0.0.1:${address.port}/index.html`;
  return {
    url,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      }),
  };
}

async function resolveDevUrl(): Promise<{ url?: string; devServer?: StaticDevServerHandle }> {
  if (process.env.POLARIS_DEV_SERVER_URL) {
    return { url: process.env.POLARIS_DEV_SERVER_URL };
  }

  const distIndexCandidates = [
    path.join(repoRoot, "src", "frontend", "dist", "index.html"),
    path.join(repoRoot, "frontend", "dist", "index.html"),
  ];

  for (const distIndex of distIndexCandidates) {
    if (!fs.existsSync(distIndex)) {
      continue;
    }
    const devServer = await startStaticDevServer(path.dirname(distIndex));
    return { url: devServer.url, devServer };
  }

  return {};
}

function appendLogLines(target: string[], chunk: Buffer | string): void {
  const text = String(chunk ?? "");
  if (!text) {
    return;
  }
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0);
  target.push(...lines);
}

function mergeCorsOrigins(existing: string | undefined, additionalOrigin: string): string {
  const items = String(existing || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (!items.includes(additionalOrigin)) {
    items.push(additionalOrigin);
  }
  return items.join(",");
}

export const test = base.extend<Fixtures>({
  mainProcessLogs: async ({ }, use) => {
    const logs: MainProcessLogs = {
      stdout: [],
      stderr: [],
    };
    await use(logs);
  },
  testEnv: async ({ }, use) => {
    const isolatedE2EHome = createIsolatedE2EHome();
    const isolatedRuntimeRoot = createIsolatedRuntimeRoot(isolatedE2EHome);
    const useRealSettings = process.env.POLARIS_E2E_USE_REAL_SETTINGS === "1";

    try {
      await use({
        isolatedE2EHome,
        isolatedRuntimeRoot,
        useRealSettings,
      });
    } finally {
      cleanupIsolatedE2EHome(isolatedE2EHome);
    }
  },
  electronApp: async ({ mainProcessLogs, testEnv }, use) => {
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      POLARIS_E2E: "1",
      POLARIS_E2E_ALLOW_MULTI_INSTANCE: "1",
    };
    delete env.ELECTRON_RUN_AS_NODE;

    if (!testEnv.useRealSettings) {
      env.POLARIS_HOME = testEnv.isolatedE2EHome;
      env.POLARIS_RUNTIME_ROOT = testEnv.isolatedRuntimeRoot;
      env.POLARIS_STATE_TO_RAMDISK = "0";
    }

    const venvPython = resolveVenvPython();
    if (venvPython && !env.POLARIS_PYTHON) {
      env.POLARIS_PYTHON = venvPython;
    }
    if (!testEnv.useRealSettings && !env.POLARIS_WORKSPACE) {
      env.POLARIS_WORKSPACE = repoRoot;
      env.POLARIS_SELF_UPGRADE_MODE = "1";
    }

    const devUrl = await resolveDevUrl();
    if (devUrl.url) {
      env.POLARIS_DEV_SERVER_URL = devUrl.url;
      try {
        const devOrigin = new URL(devUrl.url).origin;
        env.POLARIS_CORS_ORIGINS = mergeCorsOrigins(env.POLARIS_CORS_ORIGINS, devOrigin);
      } catch {
        // Ignore invalid URL parsing.
      }
    }

    let app: ElectronApplication | null = null;
    let appProcess: ReturnType<ElectronApplication["process"]> | null = null;
    const onStdout = (chunk: Buffer | string): void => {
      appendLogLines(mainProcessLogs.stdout, chunk);
    };
    const onStderr = (chunk: Buffer | string): void => {
      appendLogLines(mainProcessLogs.stderr, chunk);
    };

    try {
      app = await electron.launch({
        args: [electronMain],
        env,
      });
      appProcess = app.process();
      appProcess.stdout?.on("data", onStdout);
      appProcess.stderr?.on("data", onStderr);

      await use(app);
    } finally {
      appProcess?.stdout?.off("data", onStdout);
      appProcess?.stderr?.off("data", onStderr);
      if (app) {
        await app.close();
      }
      if (devUrl.devServer) {
        await devUrl.devServer.close();
      }
    }
  },
  window: async ({ electronApp }, use) => {
    const page = await electronApp.firstWindow();
    await page.waitForLoadState("domcontentloaded");

    // Auto-dismiss engine failure dialog - persistent handler
    const dismissDialog = async () => {
      try {
        // Try multiple dialog selectors
        const selectors = [
          page.getByRole("alertdialog", { name: /引擎执行失败|Engine.*fail|Engine Error/i }),
          page.locator("[role='alertdialog']").filter({ hasText: /失败|error|Error/i }).first(),
        ];

        for (const dialog of selectors) {
          if (await dialog.count() === 0) continue;
          const closeButton = dialog.getByRole("button", { name: /关闭|Close|OK|确定/i });
          if (await closeButton.count() > 0 && await closeButton.isVisible().catch(() => false)) {
            await closeButton.click();
            await page.waitForTimeout(500);
          }
        }
      } catch {
        // Ignore errors
      }
    };

    // Handle dialog events as they happen
    page.on("dialog", async (dialog) => {
      console.log("[fixtures] Auto-dismissing dialog:", dialog.message());
      await dialog.dismiss();
    });

    // Initial dismiss attempt
    await dismissDialog();

    // Set up interval to keep dismissing if dialog reappears
    const dialogInterval = setInterval(dismissDialog, 2000);

    await use(page);

    // Cleanup
    clearInterval(dialogInterval);
  },
});

export { expect } from "@playwright/test";
