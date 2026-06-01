const fs = require("fs");

const { stripUtf8Bom } = require("./config-paths.cjs");

function closeToTrayFromSettings(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return true;
  }
  if (typeof payload.close_to_tray === "boolean") {
    return payload.close_to_tray;
  }
  if (typeof payload.run_in_background_on_close === "boolean") {
    return payload.run_in_background_on_close;
  }
  return true;
}

function readCloseToTraySetting(settingsPath) {
  try {
    if (!settingsPath || !fs.existsSync(settingsPath)) {
      return true;
    }
    const raw = stripUtf8Bom(fs.readFileSync(settingsPath, "utf8"));
    return closeToTrayFromSettings(JSON.parse(raw));
  } catch (err) {
    console.warn(`[window] Failed to read close_to_tray setting: ${err.message}`);
    return true;
  }
}

module.exports = {
  closeToTrayFromSettings,
  readCloseToTraySetting,
};
