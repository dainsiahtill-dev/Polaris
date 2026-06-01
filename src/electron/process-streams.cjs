const IGNORED_PROCESS_STREAM_WRITE_CODES = new Set([
  "EPIPE",
  "ERR_STREAM_DESTROYED",
  "ERR_STREAM_WRITE_AFTER_END",
]);

function isIgnorableProcessStreamWriteError(error) {
  if (!error) {
    return false;
  }
  const code = String(error.code || "").trim();
  if (IGNORED_PROCESS_STREAM_WRITE_CODES.has(code)) {
    return true;
  }
  const message = String(error.message || error || "").toLowerCase();
  return (
    message.includes("broken pipe") ||
    message.includes("stream destroyed") ||
    message.includes("write after end")
  );
}

function safeWriteProcessStream(stream, chunk) {
  if (!stream || typeof stream.write !== "function") {
    return false;
  }
  if (stream.destroyed || stream.closed || stream.writableEnded || stream.writable === false) {
    return false;
  }

  try {
    return Boolean(stream.write(chunk, (error) => {
      if (!error || isIgnorableProcessStreamWriteError(error)) {
        return;
      }
      if (typeof stream.emit === "function") {
        stream.emit("polaris-write-error", error);
      }
    }));
  } catch (error) {
    return false;
  }
}

function attachProcessStreamErrorGuard(stream) {
  if (!stream || typeof stream.on !== "function") {
    return;
  }
  if (stream.__polarisProcessStreamErrorGuardAttached) {
    return;
  }
  Object.defineProperty(stream, "__polarisProcessStreamErrorGuardAttached", {
    configurable: false,
    enumerable: false,
    value: true,
  });
  stream.on("error", (error) => {
    if (isIgnorableProcessStreamWriteError(error)) {
      return;
    }
    if (typeof stream.emit === "function") {
      stream.emit("polaris-write-error", error);
    }
  });
}

module.exports = {
  attachProcessStreamErrorGuard,
  isIgnorableProcessStreamWriteError,
  safeWriteProcessStream,
};
