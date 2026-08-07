import type { SessionStatus } from "../types";

const STATUS_CONFIG: Record<SessionStatus, { label: string; dot: string; text: string; pulse?: boolean }> = {
  running: { label: "Working", dot: "bg-accent-to", text: "text-accent-to", pulse: true },
  awaiting_user: { label: "Needs you", dot: "bg-amber-400", text: "text-amber-300", pulse: true },
  starting: { label: "Starting", dot: "bg-slate-400", text: "text-slate-300", pulse: true },
  done: { label: "Done", dot: "bg-emerald-400", text: "text-emerald-300" },
  idle: { label: "Idle", dot: "bg-zinc-500", text: "text-zinc-400" },
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
