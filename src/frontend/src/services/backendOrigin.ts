/**
 * Backend origin policy for the renderer.
 *
 * The shipped connect-src policy permits the page origin and loopback
 * origins only. A backend on any other address can never be reached from
 * the page, so the renderer must not address it directly: it has to use
 * the same-origin dev proxy, which routes to the backend of the instance
 * that serves the page.
 */
export function isBackendOriginPermitted(backendUrl: string, pageOrigin: string): boolean {
  try {
    const url = new URL(backendUrl);
    if (pageOrigin && url.origin === pageOrigin) {
      return true;
    }
    return url.hostname === 'localhost' || url.hostname === '127.0.0.1';
  } catch {
    return false;
  }
}
