const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  closeToTrayFromSettings,
  readCloseToTraySetting,
} = require("./app-close-policy.cjs");

test("closeToTrayFromSettings defaults to tray behavior for compatibility", () => {
  assert.equal(closeToTrayFromSettings({}), true);
  assert.equal(closeToTrayFromSettings(null), true);
  assert.equal(closeToTrayFromSettings({ close_to_tray: "false" }), true);
});

test("closeToTrayFromSettings honors explicit close_to_tray boolean", () => {
  assert.equal(closeToTrayFromSettings({ close_to_tray: true }), true);
  assert.equal(closeToTrayFromSettings({ close_to_tray: false }), false);
});

test("closeToTrayFromSettings accepts legacy run_in_background_on_close alias", () => {
  assert.equal(closeToTrayFromSettings({ run_in_background_on_close: true }), true);
  assert.equal(closeToTrayFromSettings({ run_in_background_on_close: false }), false);
});

test("readCloseToTraySetting reads UTF-8 settings file", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "polaris-close-policy-"));
  const settingsPath = path.join(tempRoot, "settings.json");

  try {
    fs.writeFileSync(settingsPath, "\uFEFF" + JSON.stringify({ close_to_tray: false }), "utf8");
    assert.equal(readCloseToTraySetting(settingsPath), false);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("readCloseToTraySetting falls back to tray behavior for missing or invalid files", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "polaris-close-policy-invalid-"));
  const settingsPath = path.join(tempRoot, "settings.json");
  const originalWarn = console.warn;

  try {
    console.warn = () => {};
    assert.equal(readCloseToTraySetting(settingsPath), true);
    fs.writeFileSync(settingsPath, "{", "utf8");
    assert.equal(readCloseToTraySetting(settingsPath), true);
  } finally {
    console.warn = originalWarn;
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
