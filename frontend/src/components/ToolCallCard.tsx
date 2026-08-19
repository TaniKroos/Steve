import { useState } from "react";
import {
  Check,
  ChevronRight,
  Diff,
  Eye,
  FilePlus2,
  FileSearch,
  GitPullRequest,
  Key,
  Loader2,
  MessageCircle,
  ShieldCheck,
  Terminal,
  Timer,
  Undo2,
  X,
} from "lucide-react";
import type { ToolCall } from "../types";
import { DiffLines } from "./DiffLines";

// Every real tool name agent_loop/app/tools/registry.py currently
// registers, plus a generic fallback below for any future one this
// doesn't list yet -- `ToolName` is deliberately a plain `string` (see
// types.ts) so a new tool never needs a frontend change to render at all,
// just to render *well*.
const TOOL_META: Record<string, { icon: typeof Terminal; label: string }> = {
  shell_exec: { icon: Terminal, label: "Shell" },
  shell_view: { icon: Terminal, label: "Shell" },
  shell_write: { icon: Terminal, label: "Shell" },
  shell_kill: { icon: Terminal, label: "Shell" },
  open_file: { icon: FileSearch, label: "Read file" },
  str_replace: { icon: Diff, label: "Edit file" },
  create_file: { icon: FilePlus2, label: "Create file" },
  insert_at_line: { icon: Diff, label: "Edit file" },
  undo_edit: { icon: Undo2, label: "Undo edit" },
  git_create_pr: { icon: GitPullRequest, label: "Create PR" },
  git_update_pr_description: { icon: GitPullRequest, label: "Update PR" },
  git_view_pr: { icon: Eye, label: "View PR" },
  git_pr_checks: { icon: ShieldCheck, label: "CI checks" },
  git_list_repos: { icon: Eye, label: "List repos" },
  message_user: { icon: MessageCircle, label: "Message" },
  wait: { icon: Timer, label: "Wait" },
  list_secrets: { icon: Key, label: "Secrets" },
};

const DEFAULT_META = { icon: Terminal, label: "Tool" };

export function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(call.status !== "success");
  const meta = TOOL_META[call.tool] ?? { ...DEFAULT_META, label: call.tool };
  const Icon = meta.icon;

  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.02]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition hover:bg-white/[0.02]"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-white/[0.05] text-zinc-400">
          <Icon size={13} strokeWidth={2} />
        </span>

        <span className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{meta.label}</span>

        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-zinc-300">{call.summary}</span>

        {call.meta && <span className="shrink-0 font-mono text-[11px] text-zinc-600">{call.meta}</span>}

        <StatusIcon status={call.status} />

        {call.detail && (
          <ChevronRight
            size={14}
            className={`shrink-0 text-zinc-600 transition-transform ${open ? "rotate-90" : ""}`}
          />
        )}
      </button>

      {open && call.detail && (
        <div className="border-t border-white/[0.06] bg-black/30">
          <pre className="max-h-56 overflow-auto py-2.5 font-mono text-[12px] leading-relaxed">
            <DiffLines text={call.detail} />
          </pre>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ToolCall["status"] }) {
  if (status === "running") {
    return <Loader2 size={14} className="shrink-0 animate-spin text-accent-to" />;
  }
  if (status === "error") {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-zinc-500/15 text-zinc-300">
        <X size={10} strokeWidth={3} />
      </span>
    );
  }
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-white/10 text-zinc-200">
      <Check size={10} strokeWidth={3} />
    </span>
  );
}
