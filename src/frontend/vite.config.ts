import { defineConfig } from "vite";
import path from "path";
import { fileURLToPath } from "url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";

// ═══════════════════════════════════════════════════════════════════════════════
// Default Configuration Constants
// ═══════════════════════════════════════════════════════════════════════════════
const DEFAULT_BACKEND_PORT = 49977;
const DEFAULT_RENDERER_PORT = 5173;

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const rendererPortRaw = Number(process.env.KERNELONE_RENDERER_PORT || DEFAULT_RENDERER_PORT);
const rendererPort = Number.isFinite(rendererPortRaw) && rendererPortRaw > 0 ? rendererPortRaw : DEFAULT_RENDERER_PORT;
const backendPort = process.env.KERNELONE_BACKEND_PORT || String(DEFAULT_BACKEND_PORT);
const backendHttpTarget = `http://127.0.0.1:${backendPort}`;
const backendWsTarget = `ws://127.0.0.1:${backendPort}`;

function getNodeModulePackageName(normalizedId: string): string | null {
  const marker = "/node_modules/";
  const markerIndex = normalizedId.lastIndexOf(marker);
  if (markerIndex < 0) {
    return null;
  }

  const packagePath = normalizedId.slice(markerIndex + marker.length);
  const [firstPart, secondPart] = packagePath.split("/");
  if (!firstPart) {
    return null;
  }
  if (firstPart.startsWith("@")) {
    return secondPart ? `${firstPart}/${secondPart}` : null;
  }
  return firstPart;
}

function manualChunks(id: string): string | undefined {
  const normalizedId = id.replace(/\\/g, "/");
  const packageName = getNodeModulePackageName(normalizedId);

  if (packageName) {
    if (
      packageName === "react" ||
      packageName === "react-dom" ||
      packageName === "scheduler"
    ) {
      return "vendor-react";
    }

    if (
      packageName.startsWith("@radix-ui/") ||
      packageName === "cmdk" ||
      packageName === "vaul" ||
      packageName === "lucide-react" ||
      packageName === "react-icons" ||
      packageName === "class-variance-authority" ||
      packageName === "clsx" ||
      packageName === "tailwind-merge" ||
      packageName === "sonner" ||
      packageName === "framer-motion" ||
      packageName === "next-themes"
    ) {
      return "vendor-ui";
    }

    if (packageName === "xterm" || packageName === "xterm-addon-fit") {
      return "vendor-terminal";
    }

    if (
      packageName === "three" ||
      packageName === "@react-three/drei" ||
      packageName === "@react-three/fiber" ||
      packageName === "react-reconciler" ||
      packageName === "zustand"
    ) {
      return "vendor-three";
    }

    if (packageName.startsWith("@xyflow/")) {
      return "vendor-flow";
    }

    if (packageName === "recharts") {
      return "vendor-charts";
    }
  }

  if (normalizedId.includes("/src/app/components/llm/")) {
    return "feature-llm";
  }

  // 合并 feature-common 与 feature-pm 以打破循环依赖
  // feature-pm -> feature-common -> feature-pm
  if (
    normalizedId.includes("/src/app/components/common/") ||
    normalizedId.includes("/src/app/components/pm/")
  ) {
    return "feature-pm-common";
  }

  if (
    normalizedId.includes("/src/app/components/arsenal/") ||
    normalizedId.includes("/src/app/components/court/")
  ) {
    return "feature-visual";
  }

  if (
    normalizedId.includes("/src/app/components/PtyDrawer.tsx") ||
    normalizedId.includes("/src/app/components/TerminalPanel.tsx")
  ) {
    return "feature-terminal";
  }

  // feature-director 单独处理
  if (normalizedId.includes("/src/app/components/director/")) {
    return "feature-director";
  }

  return undefined;
}

export default defineConfig({
  root: rootDir,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(rootDir, "./src"),
    },
  },
  build: {
    outDir: path.join(rootDir, "dist"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
  server: {
    port: rendererPort,
    strictPort: true,
    proxy: {
      "/llm": {
        target: backendHttpTarget,
        changeOrigin: true,
      },
      "/api": {
        target: backendHttpTarget,
        changeOrigin: true,
      },
      "/ws": {
        target: backendWsTarget,
        ws: true,
      },
      "/v2": {
        target: backendHttpTarget,
        changeOrigin: true,
      },
    },
  },
});
