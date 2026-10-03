/*
 * Runtime configuration for the mobile client.
 *
 * API calls and the realtime WebSocket go through the same origin: server/server.js
 * proxies /api/v1 and /ws to the deployed backend (SECO_API_ORIGIN), so the
 * browser never hits CORS. Served from the API domain itself, nothing changes.
 */
window.SECO_CONFIG = Object.freeze({
  apiBaseUrl: '/api/v1',
  // Realtime: charging session ticks, in-app notifications, support requests.
  wsPath: '/ws',
  requestTimeoutMs: 30000,
  // Fallback when the WebSocket is down: the member's current session.
  sessionPollIntervalMs: 5000,
  stationsRefreshMs: 60000,
  authStorageKey: 'seco.accessToken',
  userStorageKey: 'seco.user'
});
