// Thin client for talking to the backend (cloudagent-backend, :8000 in
// local dev). Two things every call here needs, and easy to forget if
// you write a bare `fetch` elsewhere instead of going through this file:
//
// 1. `credentials: "include"` -- the backend uses a signed cookie
//    (Starlette's SessionMiddleware) to track login state, not a bearer
//    token. Without this, the browser won't send/accept that cookie on
//    cross-origin requests (frontend on :5173, backend on :8000), and
//    every call will look like you're logged out even right after login.
// 2. An absolute URL against VITE_BACKEND_URL -- relative fetches would
//    hit the Vite dev server on :5173, not the backend.
import type { CurrentUser, GithubRepo, RemoteMessage, RemoteSession } from "../types";

const BASE_URL = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  // Not using TS constructor-parameter-property shorthand here -- this
  // project's tsconfig has `erasableSyntaxOnly` on (Vite/esbuild strips
  // types without running real TS compilation, so any TS syntax that
  // generates actual JS, like that shorthand, isn't allowed).
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!res.ok) {
    // FastAPI's default error shape is `{"detail": "..."}` -- see
    // app/exceptions.py's handlers and get_current_user's HTTPException.
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }

  // 204 No Content (e.g. logout) has no body to parse.
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  // Not a fetch -- this is a real page navigation target (see
  // LoginScreen.tsx). The browser has to actually leave the SPA, go
  // through GitHub, and come back; you cannot do that with `fetch`.
  loginUrl: `${BASE_URL}/api/auth/login`,

  // Same deal -- GitHub's App-install page is also a real page the
  // browser has to visit, not something `fetch` can do. See
  // routers/github.py's `/install`.
  installUrl: `${BASE_URL}/api/github/install`,

  me: () => apiFetch<CurrentUser>("/api/auth/me"),

  logout: () => apiFetch<void>("/api/auth/logout", { method: "POST" }),

  // May trigger a real GitHub call server-side if the installation's
  // cache is stale (see GithubService.list_repos_for_user) -- from the
  // frontend's point of view it's still just a GET, the staleness check
  // is entirely the backend's concern.
  repos: () => apiFetch<GithubRepo[]>("/api/github/repos"),

  // A 503 here means Agent Loop rejected/couldn't be reached for the
  // hand-off (SessionService.create_session tears the sandbox back down
  // in that case) -- `ApiError.status` is how the caller tells that
  // apart from a genuine failure -- see NewSessionModal.tsx.
  createSession: (repoId: string, initialMessage: string) =>
    apiFetch<RemoteSession>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ repo_id: repoId, initial_message: initialMessage }),
    }),

  sessions: () => apiFetch<RemoteSession[]>("/api/sessions"),

  session: (id: string) => apiFetch<RemoteSession>(`/api/sessions/${id}`),

  messages: (id: string) => apiFetch<RemoteMessage[]>(`/api/sessions/${id}/messages`),

  // 202 Accepted, no body -- see routers/sessions.py's send_message.
  // Only meaningful while the session is actually `blocked` on a
  // message_user(BLOCK) tool call (FR-9); Agent Loop has no running
  // worker to deliver this to otherwise.
  sendMessage: (id: string, text: string) =>
    apiFetch<void>(`/api/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),

  // Not a fetch -- handed to `EventSource`, which manages the connection
  // itself (including auto-reconnect on a transient drop). See
  // lib/useSessionChat.ts.
  eventsUrl: (id: string) => `${BASE_URL}/api/sessions/${id}/events`,

  // --- Live workspace view (claude/live-workspace-view-plan.md §2/§3) ---
  // All four are plain pull requests, called on demand (panel open, a
  // file click, a status flip to "blocked") -- never in response to
  // every SSE event, which is the whole point of the design (see the
  // plan's §3.1 on why `file_edit` itself carries no content). A 409
  // here means `SessionNotActive` on the backend -- no live Agent Loop
  // owner to ask right now; callers treat that as "not available", not
  // a hard error (see lib/useSandboxWorkspace.ts).

  listFiles: (id: string) => apiFetch<string[]>(`/api/sessions/${id}/files`),

  fileContent: (id: string, path: string) =>
    apiFetch<{ content: string }>(`/api/sessions/${id}/files/content?path=${encodeURIComponent(path)}`),

  // The file's content as of HEAD -- the "before" half of Monaco's
  // DiffEditor (claude/live-workspace-v2.md §4.1), which needs two full
  // text blobs to diff itself rather than a unified patch string.
  fileOriginal: (id: string, path: string) =>
    apiFetch<{ content: string }>(`/api/sessions/${id}/files/original?path=${encodeURIComponent(path)}`),

  fileDiff: (id: string, path: string) =>
    apiFetch<{ diff: string }>(`/api/sessions/${id}/files/diff?path=${encodeURIComponent(path)}`),

  cumulativeDiff: (id: string) => apiFetch<{ diff: string }>(`/api/sessions/${id}/diff`),
};
