import type { SessionStatus } from "../types";

// Real per-status hue, not just brightness -- a monochrome palette
// collapsed every status into shades of the same gray, which is what
// made sessions hard to tell apart at a glance in the sidebar.
// Conventional status-color semantics: blue for in-progress (matches the
// app's own accent color, so "blue = active" reads as one consistent
// idea), amber for "needs a human," red for a real failure, gray for
// settled/no-action-needed.
const STATUS_CONFIG: Record<SessionStatus, { label: string; dot: string; text: string; pulse?: boolean }> = {
  starting: { label: "Starting", dot: "bg-blue-400", text: "text-blue-300", pulse: true },
  running: { label: "Working", dot: "bg-blue-500", text: "text-blue-300", pulse: true },
  blocked: { label: "Needs you", dot: "bg-amber-400", text: "text-amber-300", pulse: true },
  idle: { label: "Idle", dot: "bg-zinc-500", text: "text-zinc-400" },
  failed: { label: "Failed", dot: "bg-red-500", text: "text-red-400" },
};

export function StatusDot({ status }: { status: SessionStatus }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      {cfg.pulse && (
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${cfg.dot} opacity-60`} />
      )}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${cfg.dot}`} />
    </span>
  );
}

export function StatusBadge({ status }: { status: SessionStatus }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-xs font-medium ${cfg.text}`}
    >
      <StatusDot status={status} />
      {cfg.label}
    </span>
  );
}
