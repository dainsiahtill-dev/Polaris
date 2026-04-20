const { contextBridge, ipcRenderer } = require("electron");

function ensureObject(value, fieldName) {
  if (value === undefined || value === null) {
    return {};
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${fieldName} must be an object`);
  }
  return value;
}

function ensureString(value, fieldName, { maxLength = 8192, allowEmpty = false } = {}) {
  if (typeof value !== "string") {
    throw new TypeError(`${fieldName} must be a string`);
  }
  const normalized = value.trim();
  if (!allowEmpty && normalized.length === 0) {
    throw new TypeError(`${fieldName} cannot be empty`);
  }
  if (normalized.length > maxLength) {
    throw new TypeError(`${fieldName} exceeds maxLength=${maxLength}`);
  }
  return normalized;
}

function ensureInteger(value, fieldName, { min = 1, max = 10000 } = {}) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) {
    throw new TypeError(`${fieldName} must be an integer`);
  }
  if (parsed < min || parsed > max) {
    throw new TypeError(`${fieldName} must be in range [${min}, ${max}]`);
  }
  return parsed;
}

function sanitizePtyStartOptions(options) {
  const payload = ensureObject(options, "options");
  const command = payload.command === undefined
    ? undefined
    : ensureString(payload.command, "options.command", { maxLength: 512 });
  const args = Array.isArray(payload.args)
    ? payload.args.map((item, idx) => ensureString(item, `options.args[${idx}]`, { maxLength: 2048, allowEmpty: true }))
    : [];
  const cwd = payload.cwd === undefined
    ? undefined
    : ensureString(payload.cwd, "options.cwd", { maxLength: 2048 });
  const cols = payload.cols === undefined ? undefined : ensureInteger(payload.cols, "options.cols", { min: 20, max: 800 });
  const rows = payload.rows === undefined ? undefined : ensureInteger(payload.rows, "options.rows", { min: 5, max: 400 });
  const env = payload.env === undefined ? undefined : ensureObject(payload.env, "options.env");
  const useConpty = payload.use_conpty === undefined ? undefined : Boolean(payload.use_conpty);
  return {
    ...(command !== undefined ? { command } : {}),
    ...(args.length > 0 ? { args } : {}),
    ...(cwd !== undefined ? { cwd } : {}),
    ...(cols !== undefined ? { cols } : {}),
    ...(rows !== undefined ? { rows } : {}),
    ...(env !== undefined ? { env } : {}),
    ...(useConpty !== undefined ? { use_conpty: useConpty } : {}),
  };
}

contextBridge.exposeInMainWorld("polaris", {
  getBackendInfo: () => ipcRenderer.invoke("hp:get-backend"),
  getBackendStatus: () => ipcRenderer.invoke("hp:backend-status"),
  pickWorkspace: (options) => ipcRenderer.invoke("hp:pick-workspace", ensureObject(options, "options")),
  openPath: (targetPath) => ipcRenderer.invoke("hp:open-path", ensureString(targetPath, "targetPath")),
  secrets: {
    available: () => ipcRenderer.invoke("hp:secrets-available"),
    get: (key) => ipcRenderer.invoke("hp:secrets-get", ensureString(key, "key", { maxLength: 128 })),
    set: (key, value) => ipcRenderer.invoke("hp:secrets-set", {
      key: ensureString(key, "key", { maxLength: 128 }),
      value: ensureString(value, "value", { maxLength: 8192, allowEmpty: true }),
    }),
    remove: (key) => ipcRenderer.invoke("hp:secrets-delete", ensureString(key, "key", { maxLength: 128 })),
  },
  pty: {
    start: (options) => ipcRenderer.invoke("hp:pty-start", sanitizePtyStartOptions(options)),
    write: (id, data) => ipcRenderer.invoke("hp:pty-write", {
      id: ensureString(id, "id", { maxLength: 128 }),
      data: ensureString(data, "data", { maxLength: 200000, allowEmpty: true }),
    }),
    resize: (id, cols, rows) => ipcRenderer.invoke("hp:pty-resize", {
      id: ensureString(id, "id", { maxLength: 128 }),
      cols: ensureInteger(cols, "cols", { min: 20, max: 800 }),
      rows: ensureInteger(rows, "rows", { min: 5, max: 400 }),
    }),
    close: (id) => ipcRenderer.invoke("hp:pty-close", {
      id: ensureString(id, "id", { maxLength: 128 }),
    }),
    onData: (handler) => {
      if (typeof handler !== "function") {
        throw new TypeError("handler must be a function");
      }
      const listener = (_event, payload) => handler?.(payload);
      ipcRenderer.on("hp:pty-data", listener);
      return () => ipcRenderer.removeListener("hp:pty-data", listener);
    },
    onExit: (handler) => {
      if (typeof handler !== "function") {
        throw new TypeError("handler must be a function");
      }
      const listener = (_event, payload) => handler?.(payload);
      ipcRenderer.on("hp:pty-exit", listener);
      return () => ipcRenderer.removeListener("hp:pty-exit", listener);
    },
  },
  windowControl: {
    minimize: () => ipcRenderer.invoke("hp:window-minimize"),
    maximize: () => ipcRenderer.invoke("hp:window-maximize"),
    close: () => ipcRenderer.invoke("hp:window-close"),
    getState: () => ipcRenderer.invoke("hp:window-get-state"),
  },
  notification: {
    show: (options) => ipcRenderer.invoke("hp:notification-show", {
      title: options.title,
      body: options.body,
      silent: options.silent,
    }),
  },
  onAction: (handler) => {
    if (typeof handler !== "function") {
      throw new TypeError("handler must be a function");
    }
    const listener = (_event, payload) => handler?.(payload);
    ipcRenderer.on("hp:action", listener);
    return () => ipcRenderer.removeListener("hp:action", listener);
  },
});
