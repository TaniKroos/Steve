import type { SessionStatus } from "../types";

const STATUS_CONFIG: Record<SessionStatus, { label: string; dot: string; text: string; pulse?: boolean }> = {
  starting: { label: "Starting", dot: "bg-zinc-400", text: "text-zinc-300", pulse: true },
  running: { label: "Working", dot: "bg-zinc-100", text: "text-zinc-100", pulse: true },
  blocked: { label: "Needs you", dot: "bg-zinc-300", text: "text-zinc-200", pulse: true },
  idle: { label: "Idle", dot: "bg-zinc-500", text: "text-zinc-400" },
  failed: { label: "Failed", dot: "bg-zinc-500", text: "text-zinc-400" },
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
