import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChatView } from "../components/ChatView";
import { SandboxPanel } from "../components/SandboxPanel";
import { Sidebar } from "../components/Sidebar";
import { api } from "../lib/api";
import { activeSessionId, messagesBySession, sessions } from "../lib/mockData";
import type { CurrentUser } from "../types";

export function AppShell() {
  const navigate = useNavigate();

  // `undefined` = still checking, `null` = checked and not logged in.
  // Three states rather than a boolean so the "checking" render (a blank
  // screen, momentarily) is distinguishable from "confirmed logged out" --
  // otherwise there'd be a flash of "logged out" UI on every real login too.
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined);

  useEffect(() => {
    // This is the entire point of this effect: GitHub OAuth never touches
    // this component directly (see routers/auth.py -- the whole exchange
    // happens server-side before the browser ever lands here). All this
    // page can do is ask the backend "is the cookie you just gave my
    // browser actually valid" via /api/auth/me, using it as a real
    // authorization check, not just a UI nicety.
    api
      .me()
      .then(setUser)
      .catch(() => {
        setUser(null);
        navigate("/", { replace: true });
      });
  }, [navigate]);

  const [selectedId, setSelectedId] = useState(activeSessionId);
  const [sandboxOpen, setSandboxOpen] = useState(true);
  const session = sessions.find((s) => s.id === selectedId) ?? sessions[0];
  const messages = messagesBySession[selectedId] ?? [];

  const handleLogout = () => {
    api.logout().finally(() => navigate("/", { replace: true }));
  };

  if (!user) {
    // Covers both "still checking" (undefined) and the brief instant
    // before the redirect above actually navigates away (null) --
    // deliberately not the real app UI in either case, so nothing here
    // ever renders session data on top of an unauthenticated request.
    return <div className="flex h-screen w-screen items-center justify-center bg-canvas text-zinc-500" />;
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-canvas">
      <Sidebar
        sessions={sessions}
        activeId={selectedId}
        onSelect={setSelectedId}
        user={user}
        onLogout={handleLogout}
      />
      <ChatView
        session={session}
        messages={messages}
        sandboxOpen={sandboxOpen}
        onToggleSandbox={() => setSandboxOpen((o) => !o)}
      />
      {sandboxOpen && <SandboxPanel session={session} onClose={() => setSandboxOpen(false)} />}
    </div>
  );
}
