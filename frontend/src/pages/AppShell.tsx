import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChatView } from "../components/ChatView";
import { NewSessionModal } from "../components/NewSessionModal";
import { SandboxPanel } from "../components/SandboxPanel";
import { Sidebar } from "../components/Sidebar";
import { api } from "../lib/api";
import { toUiSession } from "../lib/transform";
import { useSessionChat } from "../lib/useSessionChat";
import type { CurrentUser, GithubRepo, RemoteSession } from "../types";

// How often the session list re-fetches while at least one session isn't
// in a terminal-ish state -- there's no push channel for "a session's
// status/PR fields changed" the way there is for chat content (that's
// what the per-session SSE connection in useSessionChat is for), so a
// short poll is the simplest thing that keeps the sidebar and header
// honest without adding a second live channel just for this.
const SESSION_POLL_MS = 5000;

export function AppShell() {
  const navigate = useNavigate();

  // `undefined` = still checking, `null` = checked and not logged in.
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => {
        setUser(null);
        navigate("/", { replace: true });
      });
  }, [navigate]);

  const [repos, setRepos] = useState<GithubRepo[]>([]);

  useEffect(() => {
    if (!user) return;
    api.repos().then(setRepos).catch(() => setRepos([]));
  }, [user]);

  const [sessions, setSessions] = useState<RemoteSession[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Fallback titles for sessions created this tab session, keyed by id --
  // see NewSessionModal's onCreated below. Not persisted anywhere; a
  // page refresh loses it and falls back to the repo-name title instead,
  // which is an acceptable trade for not touching the backend schema
  // just for this.
  const fallbackTitles = useRef<Record<string, string>>({});

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    api
      .sessions()
      .then((remote) => {
        if (cancelled) return;
        setSessions(remote);
        setSelectedId((current) => current ?? remote[0]?.id ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user]);

  // Keep the list fresh -- see SESSION_POLL_MS's comment above for why
  // this is a poll rather than another SSE connection.
  useEffect(() => {
    if (!user) return;
    const hasLiveSession = sessions.some((s) => s.status === "starting" || s.status === "running" || s.status === "blocked");
    if (!hasLiveSession) return;
    const interval = setInterval(() => {
      api.sessions().then(setSessions).catch(() => {});
    }, SESSION_POLL_MS);
    return () => clearInterval(interval);
  }, [user, sessions]);

  const patchSession = (id: string, patch: Partial<RemoteSession>) => {
    setSessions((current) => current.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  };

  const { messages, liveStatus, send } = useSessionChat(selectedId, (prUrl, prNumber) => {
    if (selectedId) patchSession(selectedId, { pr_url: prUrl, pr_number: prNumber });
  });

  const [sandboxOpen, setSandboxOpen] = useState(true);
  const [showNewSession, setShowNewSession] = useState(false);

  const uiSessions = sessions.map((s) => toUiSession(s, repos, fallbackTitles.current[s.id]));
  const selectedSession = uiSessions.find((s) => s.id === selectedId) ?? null;

  const handleLogout = () => {
    api.logout().finally(() => navigate("/", { replace: true }));
  };

  if (!user) {
    return <div className="flex h-screen w-screen items-center justify-center bg-canvas text-zinc-500" />;
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-canvas">
      <Sidebar
        sessions={uiSessions}
        activeId={selectedId}
        onSelect={setSelectedId}
        user={user}
        onLogout={handleLogout}
        repos={repos}
        onNewSession={() => setShowNewSession(true)}
      />
      {selectedSession ? (
        <ChatView
          session={selectedSession}
          messages={messages}
          liveStatus={liveStatus}
          sandboxOpen={sandboxOpen}
          onToggleSandbox={() => setSandboxOpen((o) => !o)}
          onSend={send}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-[13px] text-zinc-600">
          {sessions.length === 0 ? "Start a new session to get going." : "Select a session."}
        </div>
      )}
      {selectedSession && sandboxOpen && <SandboxPanel session={selectedSession} onClose={() => setSandboxOpen(false)} />}
      {showNewSession && (
        <NewSessionModal
          repos={repos}
          onClose={() => setShowNewSession(false)}
          onCreated={(session, initialMessage) => {
            fallbackTitles.current[session.id] = initialMessage.length > 60 ? `${initialMessage.slice(0, 57)}...` : initialMessage;
            setSessions((current) => [session, ...current]);
            setSelectedId(session.id);
            setShowNewSession(false);
          }}
        />
      )}
    </div>
  );
}
