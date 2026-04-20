/**
 * electron-builder afterPack hook.
 * Rebuilds native modules (node-pty) for the packaged Electron version.
 */
exports.default = async function afterPack(context) {
  console.log(`[afterPack] Platform: ${context.electronPlatformName}`);
  console.log(`[afterPack] Arch: ${context.arch}`);
  // electron-builder handles native module rebuild automatically
  // via the electronVersion + node-gyp integration.
  // This hook is reserved for custom post-pack steps if needed.
};
