import { ExternalLink, GitBranch, Lock, PanelRightClose, PanelRightOpen } from "lucide-react";
import type { Session } from "../types";
import { StatusBadge } from "./StatusBadge";

export function ChatHeader({
  session,
  sandboxOpen,
  onToggleSandbox,
}: {
  session: Session;
  sandboxOpen: boolean;
  onToggleSandbox: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/[0.06] bg-surface/60 px-6 py-3.5">
      <div className="min-w-0">
        <h1 className="truncate text-[14px] font-semibold text-zinc-100">{session.title}</h1>
        <div className="mt-1 flex items-center gap-2 text-[12px] text-zinc-500">
          <span className="flex items-center gap-1">
            {session.repo.private && <Lock size={10} />}
            {session.repo.owner}/{session.repo.name}
          </span>
          {session.branchName && (
            <>
              <span className="text-zinc-700">·</span>
              <span className="flex items-center gap-1 font-mono text-[11.5px] text-zinc-400">
                <GitBranch size={11} />
                {session.branchName}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        {session.prUrl && (
          <a
            href={session.prUrl}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs font-medium text-zinc-300 transition hover:border-white/20 hover:text-zinc-100"
          >
            PR #{session.prNumber}
            <ExternalLink size={11} />
          </a>
        )}
        <StatusBadge status={session.status} />
        <button
          onClick={onToggleSandbox}
          title={sandboxOpen ? "Hide sandbox" : "Show sandbox"}
          className={`flex h-7 w-7 items-center justify-center rounded-lg border transition ${
            sandboxOpen
              ? "border-white/15 bg-white/[0.06] text-zinc-200"
              : "border-white/10 bg-white/[0.03] text-zinc-500 hover:text-zinc-300"
          }`}
        >
          {sandboxOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
        </button>
      </div>
    </div>
  );
}
