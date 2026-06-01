const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");

const {
  attachProcessStreamErrorGuard,
  isIgnorableProcessStreamWriteError,
  safeWriteProcessStream,
} = require("./process-streams.cjs");

test("isIgnorableProcessStreamWriteError recognizes broken pipe errors", () => {
  assert.equal(isIgnorableProcessStreamWriteError(Object.assign(new Error("write EPIPE"), { code: "EPIPE" })), true);
  assert.equal(isIgnorableProcessStreamWriteError(new Error("EPIPE: broken pipe, write")), true);
  assert.equal(isIgnorableProcessStreamWriteError(Object.assign(new Error("other"), { code: "ECONNRESET" })), false);
});

test("safeWriteProcessStream swallows synchronous EPIPE from closed process pipes", () => {
  const stream = {
    write() {
      throw Object.assign(new Error("EPIPE: broken pipe, write"), { code: "EPIPE" });
    },
  };

  assert.doesNotThrow(() => safeWriteProcessStream(stream, "payload"));
  assert.equal(safeWriteProcessStream(stream, "payload"), false);
});

test("safeWriteProcessStream swallows asynchronous write callback EPIPE", () => {
  const stream = new EventEmitter();
  let writeCalled = false;
  stream.write = (_chunk, callback) => {
    writeCalled = true;
    callback(Object.assign(new Error("write EPIPE"), { code: "EPIPE" }));
    return true;
  };

  assert.equal(safeWriteProcessStream(stream, "payload"), true);
  assert.equal(writeCalled, true);
});

test("attachProcessStreamErrorGuard prevents duplicate handlers", () => {
  const stream = new EventEmitter();
  attachProcessStreamErrorGuard(stream);
  attachProcessStreamErrorGuard(stream);

  assert.equal(stream.listenerCount("error"), 1);
  assert.doesNotThrow(() => stream.emit("error", Object.assign(new Error("write EPIPE"), { code: "EPIPE" })));
});
