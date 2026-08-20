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
  onSend: (text: string, isResume?: boolean) => Promise<void>;
}) {
  // FR-9 + session resume (claude/session-resume-plan.md): a reply while
  // `blocked` gets delivered to the still-running worker directly; a
  // reply to an `idle` or `failed` session instead wakes a fresh worker
  // back up with the full prior conversation reloaded from Postgres --
  // backend picks between the two transparently (SessionService.forward_message),
  // so the frontend just needs to stop hard-blocking the composer for
  // the ended states too.
  const canReply = session.status === "blocked" || session.status === "idle" || session.status === "failed";
  // Only the idle/failed cases are a real "wake this back up" resume --
  // a blocked reply just reaches an already-running worker directly.
  const isResume = session.status === "idle" || session.status === "failed";
  const composerPlaceholder =
    session.status === "blocked"
      ? undefined
      : session.status === "idle"
        ? "Send a message to pick this session back up…"
        : session.status === "failed"
          ? "Session failed — send a message to try again…"
          : "Waiting for the agent…";

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
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-white/[0.04] to-transparent" />

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
        placeholder={composerPlaceholder}
        onSend={(text) => onSend(text, isResume)}
      />
    </div>
  );
}
