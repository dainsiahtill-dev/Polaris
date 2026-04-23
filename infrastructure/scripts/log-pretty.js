const DISABLE_VALUES = new Set(["0", "false", "no", "off"]);
const LEVELS = new Set(["debug", "info", "warning", "error", "critical"]);

const STYLE = {
  sourceRenderer: "30;46",
  sourceElectron: "30;45",
  sourceBackend: "30;42",
  sourceDefault: "30;47",
  levelDebug: "30;47",
  levelInfo: "30;44",
  levelWarning: "30;43",
  levelError: "37;41",
  levelCritical: "37;41",
  methodGet: "30;46",
  methodPost: "30;42",
  methodPut: "30;43",
  methodPatch: "30;45",
  methodDelete: "37;41",
  methodDefault: "30;47",
  status2xx: "30;42",
  status3xx: "30;46",
  status4xx: "30;43",
  status5xx: "37;41",
  statusDefault: "30;47",
  trace: "30;47",
  json: "30;47",
  dim: "90",
};

function envEnabled(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined) return defaultValue;
  const value = String(raw).trim().toLowerCase();
  return !DISABLE_VALUES.has(value);
}

function useColor(ttyCapable) {
  if (!ttyCapable) return false;
  if (!envEnabled("KERNELONE_PRETTY_LOGS", true)) return false;
  if (!envEnabled("KERNELONE_LOG_COLORS", true)) return false;
  if (process.env.NO_COLOR !== undefined) return false;
  return true;
}

function paint(enabled, code, text) {
  if (!enabled) return text;
  return `\u001b[${code}m${text}\u001b[0m`;
}

function badge(label, code, enabled) {
  const text = ` ${String(label).toUpperCase()} `;
  return enabled ? paint(true, code, text) : `[${String(label).toUpperCase()}]`;
}

function dim(text, enabled) {
  return paint(enabled, STYLE.dim, text);
}

function sourceStyle(name) {
  const key = String(name || "").trim().toLowerCase();
  if (key === "dev:renderer") return STYLE.sourceRenderer;
  if (key === "dev:electron") return STYLE.sourceElectron;
  if (key === "backend") return STYLE.sourceBackend;
  return STYLE.sourceDefault;
}

function levelStyle(level) {
  const key = String(level || "").trim().toLowerCase();
  if (key === "debug") return STYLE.levelDebug;
  if (key === "warning") return STYLE.levelWarning;
  if (key === "error") return STYLE.levelError;
  if (key === "critical") return STYLE.levelCritical;
  return STYLE.levelInfo;
}

function methodStyle(method) {
  const key = String(method || "").trim().toUpperCase();
  if (key === "GET") return STYLE.methodGet;
  if (key === "POST") return STYLE.methodPost;
  if (key === "PUT") return STYLE.methodPut;
  if (key === "PATCH") return STYLE.methodPatch;
  if (key === "DELETE") return STYLE.methodDelete;
  return STYLE.methodDefault;
}

function statusStyle(code) {
  const value = Number.parseInt(String(code), 10);
  if (!Number.isFinite(value)) return STYLE.statusDefault;
  if (value >= 500) return STYLE.status5xx;
  if (value >= 400) return STYLE.status4xx;
  if (value >= 300) return STYLE.status3xx;
  if (value >= 200) return STYLE.status2xx;
  return STYLE.statusDefault;
}

function extractLeadingTag(line) {
  const match = String(line || "").match(/^\[([^\]]+)\]\s*(.*)$/);
  if (!match) {
    return { tag: "", rest: String(line || "") };
  }
  return { tag: String(match[1] || ""), rest: String(match[2] || "") };
}

function parseJsonLine(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function parseUvicornAccess(text) {
  const pattern = /^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+([^\s]+)\s+-\s+"([A-Z]+)\s+([^"]+)\s+HTTP\/([0-9.]+)"\s+(\d{3})\s*(.*)$/;
  const match = String(text || "").match(pattern);
  if (!match) return null;
  return {
    level: match[1],
    client: match[2],
    method: match[3],
    path: match[4],
    statusCode: match[6],
    statusText: String(match[7] || "").trim(),
  };
}

function parseLevelMessage(text) {
  const match = String(text || "").match(/^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s*(.*)$/);
  if (!match) return null;
  return {
    level: match[1],
    message: String(match[2] || "").trim(),
  };
}

function formatDebugTraceEvent(payload, colorEnabled) {
  const event = String(payload.event || "");
  const method = String(payload.method || "").toUpperCase();
  const path = String(payload.path || "");
  const traceId = String(payload.trace_id || "");
  const shortTrace = traceId ? traceId.slice(0, 12) : "";
  const ts = String(payload.ts || "");
  const time = ts ? ts.slice(11, 19) : "";
  const timePart = time ? `${dim(time, colorEnabled)} ` : "";

  if (event === "http.in.request") {
    const badges = [
      badge("trace", STYLE.trace, colorEnabled),
      badge("req", STYLE.levelInfo, colorEnabled),
    ];
    if (method) badges.push(badge(method, methodStyle(method), colorEnabled));
    const text = `${timePart}${path || "/"}${shortTrace ? ` ${dim(`trace=${shortTrace}`, colorEnabled)}` : ""}`;
    return { badges, text };
  }

  if (event === "http.in.response") {
    const statusCode = payload.status_code;
    const duration = payload.duration_ms;
    const badges = [
      badge("trace", STYLE.trace, colorEnabled),
      badge("resp", STYLE.levelInfo, colorEnabled),
    ];
    if (method) badges.push(badge(method, methodStyle(method), colorEnabled));
    if (statusCode !== undefined && statusCode !== null && String(statusCode) !== "") {
      badges.push(badge(String(statusCode), statusStyle(statusCode), colorEnabled));
    }
    const durationPart = Number.isFinite(Number(duration)) ? ` ${dim(`${duration}ms`, colorEnabled)}` : "";
    const text = `${timePart}${path || "/"}${durationPart}${shortTrace ? ` ${dim(`trace=${shortTrace}`, colorEnabled)}` : ""}`;
    return { badges, text };
  }

  return null;
}

function summarizeJson(payload) {
  const keys = ["event", "method", "path", "status_code", "duration_ms", "trace_id"];
  const pairs = [];
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(payload, key)) continue;
    const value = payload[key];
    if (value === undefined || value === null || value === "") continue;
    pairs.push(`${key}=${String(value)}`);
  }
  if (pairs.length > 0) {
    return pairs.join(" ");
  }
  return JSON.stringify(payload);
}

function formatCoreText(text, colorEnabled) {
  const uvicorn = parseUvicornAccess(text);
  if (uvicorn) {
    const badges = [
      badge(uvicorn.level, levelStyle(uvicorn.level), colorEnabled),
      badge(uvicorn.method, methodStyle(uvicorn.method), colorEnabled),
      badge(uvicorn.statusCode, statusStyle(uvicorn.statusCode), colorEnabled),
    ];
    const statusText = uvicorn.statusText ? ` ${dim(uvicorn.statusText, colorEnabled)}` : "";
    return {
      badges,
      text: `${uvicorn.path}${statusText} ${dim(uvicorn.client, colorEnabled)}`.trim(),
    };
  }

  const leveled = parseLevelMessage(text);
  if (leveled && LEVELS.has(leveled.level.toLowerCase())) {
    return {
      badges: [badge(leveled.level, levelStyle(leveled.level), colorEnabled)],
      text: leveled.message || "-",
    };
  }

  return { badges: [], text };
}

function formatPlain(sourceTag, sourceText, innerTag, innerText) {
  const outer = `[${sourceTag}]`;
  if (innerTag) {
    return `${outer} [${innerTag}] ${innerText}`;
  }
  return `${outer} ${sourceText}`;
}

function formatLogLine(sourceTag, line, options = {}) {
  const source = String(sourceTag || "").trim() || "log";
  const raw = String(line || "").replace(/\r/g, "");
  if (!raw.trim()) return null;

  const { tag, rest } = extractLeadingTag(raw);
  const tagLower = tag.toLowerCase();
  const sourceLower = source.toLowerCase();
  const innerTag = tag && tagLower !== sourceLower ? tag : "";
  const message = tag && tagLower === sourceLower ? rest : tag ? rest : raw;

  const prettyEnabled = envEnabled("KERNELONE_PRETTY_LOGS", true);
  if (!prettyEnabled) {
    return formatPlain(source, message, innerTag, message);
  }

  const colorEnabled = useColor(Boolean(options.tty));
  const badges = [badge(source, sourceStyle(source), colorEnabled)];
  if (innerTag) {
    badges.push(badge(innerTag, sourceStyle(innerTag), colorEnabled));
  }

  const json = parseJsonLine(message);
  if (json) {
    const traceEvent = formatDebugTraceEvent(json, colorEnabled);
    if (traceEvent) {
      return `${badges.concat(traceEvent.badges).join(" ")} ${traceEvent.text}`.trimEnd();
    }
    return `${badges.join(" ")} ${badge("json", STYLE.json, colorEnabled)} ${summarizeJson(json)}`;
  }

  const core = formatCoreText(message, colorEnabled);
  const prefix = badges.concat(core.badges).join(" ");
  return `${prefix} ${core.text}`.trimEnd();
}

module.exports = {
  formatLogLine,
};
