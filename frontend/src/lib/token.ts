// The shell-server injects window.__LUMEN_TOKEN__ into the served index.html
// before the first paint (same mechanism as the vanilla webui). In dev mode
// (Vite dev server, no injection) the token is absent → API calls reach the
// proxy unauthenticated, which is fine for local dev against a non-auth backend.
export const token = (): string =>
  (window as unknown as Record<string, unknown>)['__LUMEN_TOKEN__'] as string ?? ''
