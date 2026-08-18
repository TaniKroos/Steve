import type { ChatMessage, Session } from "../types";
import { ChatHeader } from "./ChatHeader";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";

export function ChatView({
  session,
  messages,
  liveStatus,
  sandboxOpen,
  onToggleSandbox,
  onSend,
}: {
  session: Session;
  messages: ChatMessage[];
  liveStatus?: string | null;
  sandboxOpen: boolean;
  onToggleSandbox: () => void;
  onSend: (text: string) => Promise<void>;
}) {
  // FR-9: a follow-up can only actually be *delivered* while Agent Loop
  // is sitting on a `message_user(BLOCK)` tool call waiting for one --
  // there's no running worker to receive it otherwise (see
  // agent_loop/app/routers/internal.py's send_message, which 404s once
  // the session isn't active anywhere).
  const canReply = session.status === "blocked";

  return (
    // min-w-0 is load-bearing, not decorative: without it this flex item
    // refuses to shrink below its content's intrinsic width (long
    // unwrapped tool-call command text, mainly) once Sidebar + this +
    // SandboxPanel's combined width exceeds the viewport -- which forces
    // the whole AppShell row to silently overflow (clipped by its own
    // overflow-hidden, so invisible at rest) instead of this column
    // actually shrinking to its allotted space the way flex-1 implies it
    // should.
    <div className="relative flex h-screen min-w-0 flex-1 flex-col bg-canvas">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-blue-700/[0.07] to-transparent" />

      <ChatHeader session={session} sandboxOpen={sandboxOpen} onToggleSandbox={onToggleSandbox} />

      {liveStatus && (
        <div className="border-b border-white/[0.06] bg-accent-via/[0.06] px-6 py-1.5 text-center text-[11.5px] text-accent-to">
          {liveStatus}
        </div>
      )}

      <div className="relative flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 px-6 py-6">
          {messages.length === 0 ? (
            <p className="pt-12 text-center text-[13px] text-zinc-600">No messages yet.</p>
          ) : (
            messages.map((m) => <MessageBubble key={m.id} message={m} />)
          )}
        </div>
      </div>

      <Composer
        disabled={!canReply}
        placeholder={canReply ? undefined : "Waiting for the agent…"}
        onSend={onSend}
      />
    </div>
  );
}
