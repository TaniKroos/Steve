import { useState } from "react";
import { ChatView } from "../components/ChatView";
import { SandboxPanel } from "../components/SandboxPanel";
import { Sidebar } from "../components/Sidebar";
import { activeSessionId, messagesBySession, sessions } from "../lib/mockData";

export function AppShell() {
  const [selectedId, setSelectedId] = useState(activeSessionId);
  const [sandboxOpen, setSandboxOpen] = useState(true);
  const session = sessions.find((s) => s.id === selectedId) ?? sessions[0];
  const messages = messagesBySession[selectedId] ?? [];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-canvas">
      <Sidebar sessions={sessions} activeId={selectedId} onSelect={setSelectedId} />
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
