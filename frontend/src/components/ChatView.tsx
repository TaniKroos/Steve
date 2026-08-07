import type { ChatMessage, Session } from "../types";
import { ChatHeader } from "./ChatHeader";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";

export function ChatView({
  session,
  messages,
  sandboxOpen,
  onToggleSandbox,
}: {
  session: Session;
  messages: ChatMessage[];
  sandboxOpen: boolean;
  onToggleSandbox: () => void;
}) {
  return (
    <div className="relative flex h-screen flex-1 flex-col bg-canvas">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-blue-700/[0.07] to-transparent" />

      <ChatHeader session={session} sandboxOpen={sandboxOpen} onToggleSandbox={onToggleSandbox} />

      <div className="relative flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 px-6 py-6">
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
        </div>
      </div>

      <Composer disabled={session.status === "running"} />
    </div>
  );
}
