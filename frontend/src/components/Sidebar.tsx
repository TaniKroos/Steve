import { Plus, Search, Settings } from "lucide-react";
import type { Session } from "../types";
import { Logo } from "./Logo";
import { StatusDot } from "./StatusBadge";

export function Sidebar({
  sessions,
  activeId,
  onSelect,
}: {
  sessions: Session[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <aside className="flex h-screen w-[300px] shrink-0 flex-col border-r border-white/[0.06] bg-surface">
      <div className="flex items-center justify-between px-4 pb-4 pt-5">
        <Logo size="sm" />
      </div>

      <div className="px-3">
        <button className="group flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-accent-from via-accent-via to-accent-to px-3 py-2.5 text-sm font-semibold text-white shadow-[0_4px_20px_-6px_rgba(47,111,237,0.45)] transition-transform hover:scale-[1.015] active:scale-[0.985]">
          <Plus size={16} strokeWidth={2.5} />
          New session
        </button>
      </div>

      <div className="mt-4 px-3">
        <div className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-zinc-500 transition focus-within:border-white/[0.14] focus-within:text-zinc-300">
          <Search size={14} />
          <input
            placeholder="Search sessions"
            className="w-full bg-transparent text-[13px] text-zinc-200 placeholder:text-zinc-600 focus:outline-none"
          />
        </div>
      </div>

      <div className="mt-2 flex-1 overflow-y-auto px-2 py-2">
        <p className="px-2.5 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-600">
          Sessions
        </p>
        <div className="flex flex-col gap-0.5">
          {sessions.map((s) => {
            const active = s.id === activeId;
            return (
              <button
                key={s.id}
                onClick={() => onSelect(s.id)}
                className={`group relative rounded-lg px-2.5 py-2.5 text-left transition ${
                  active ? "bg-white/[0.06]" : "hover:bg-white/[0.03]"
                }`}
              >
                {active && (
                  <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-gradient-to-b from-accent-from to-accent-to" />
                )}
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={`truncate text-[13px] font-medium ${
                      active ? "text-zinc-50" : "text-zinc-300 group-hover:text-zinc-100"
                    }`}
                  >
                    {s.title}
                  </span>
                  <StatusDot status={s.status} />
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-zinc-500">
                  <span className="truncate">{s.repo.name}</span>
                  <span className="text-zinc-700">·</span>
                  <span className="shrink-0">{s.updatedAt}</span>
                  {s.unread && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent-to" />}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-white/[0.06] p-3">
        <button className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-white/[0.03]">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 text-[11px] font-semibold text-zinc-200 ring-1 ring-white/10">
            TS
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium text-zinc-200">Tanish Saini</p>
            <p className="truncate text-[11px] text-zinc-500">tanishsaini26@gmail.com</p>
          </div>
          <Settings size={15} className="shrink-0 text-zinc-600" />
        </button>
      </div>
    </aside>
  );
}
