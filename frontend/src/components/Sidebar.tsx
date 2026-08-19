import { useState } from "react";
import { ChevronRight, GitBranch, LogOut, PanelLeftClose, PanelLeftOpen, Plus, Search } from "lucide-react";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { CurrentUser, GithubRepo, Session } from "../types";
import { GithubMark } from "./GithubMark";
import { Logo } from "./Logo";
import { StatusDot } from "./StatusBadge";

export function Sidebar({
  sessions,
  activeId,
  onSelect,
  user,
  onLogout,
  repos,
  onNewSession,
  collapsed,
  onToggleCollapse,
}: {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  user: CurrentUser;
  onLogout: () => void;
  repos: GithubRepo[];
  onNewSession: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const initials = user.github_login.slice(0, 2).toUpperCase();
  const [reposOpen, setReposOpen] = useState(true);
  return (
    <aside
      className={`flex h-screen shrink-0 flex-col border-r border-white/[0.06] bg-surface transition-[width] duration-200 ${
        collapsed ? "w-[68px]" : "w-[300px]"
      }`}
    >
      <div className={`flex items-center pb-4 pt-5 ${collapsed ? "justify-center px-2" : "justify-between px-4"}`}>
        {!collapsed && <Logo size="sm" />}
        <button
          onClick={onToggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 transition hover:bg-white/[0.06] hover:text-zinc-200"
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>

      {collapsed ? (
        <div className="flex flex-col items-center gap-3 px-2">
          <button
            onClick={onNewSession}
            title="New session"
            aria-label="New session"
            className="flex h-9 w-9 items-center justify-center rounded-xl bg-zinc-100 text-zinc-900 transition hover:bg-white active:scale-[0.95]"
          >
            <Plus size={18} strokeWidth={2.5} />
          </button>
          <button
            onClick={onToggleCollapse}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 transition hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      ) : (
        <>

      <div className="px-3">
        <button
          onClick={onNewSession}
          className="group flex w-full items-center justify-center gap-2 rounded-xl bg-zinc-100 px-3 py-2.5 text-sm font-semibold text-zinc-900 shadow-[0_4px_20px_-6px_rgba(255,255,255,0.18)] transition-transform hover:scale-[1.015] hover:bg-white active:scale-[0.985]"
        >
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

      <div className="mt-4 px-3">
        <div className="flex items-center justify-between px-0.5 pb-1.5">
          {/*
            Two separate interactive elements side by side, not one
            nested inside the other -- an <a> inside a <button> is
            invalid HTML and browsers handle the click target
            inconsistently, so the toggle and the "connect" link are
            siblings even though they sit on the same row.
          */}
          <button
            onClick={() => setReposOpen((open) => !open)}
            aria-expanded={reposOpen}
            className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-600 transition hover:text-zinc-400"
          >
            <ChevronRight
              size={11}
              strokeWidth={2.5}
              className={`shrink-0 transition-transform duration-150 ${reposOpen ? "rotate-90" : ""}`}
            />
            Connected repos
            {repos.length > 0 && <span className="text-zinc-700">({repos.length})</span>}
          </button>
          <a
            href={api.installUrl}
            title="Connect a repo"
            className="flex h-5 w-5 items-center justify-center rounded text-zinc-600 transition hover:bg-white/[0.06] hover:text-zinc-300"
          >
            <Plus size={13} strokeWidth={2.5} />
          </a>
        </div>
        {reposOpen &&
          (repos.length === 0 ? (
            <a
              href={api.installUrl}
              className="flex items-center gap-2 rounded-lg border border-dashed border-white/[0.08] px-2.5 py-2.5 text-[12px] text-zinc-500 transition hover:border-white/[0.16] hover:text-zinc-300"
            >
              <GithubMark size={14} />
              Connect a repo to get started
            </a>
          ) : (
            <div className="flex max-h-[132px] flex-col gap-0.5 overflow-y-auto">
              {repos.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[12.5px] text-zinc-300"
                >
                  <GitBranch size={13} className="shrink-0 text-zinc-600" />
                  <span className="truncate">
                    {r.owner}/{r.name}
                  </span>
                  {r.private && <span className="ml-auto shrink-0 text-[10px] text-zinc-600">private</span>}
                </div>
              ))}
            </div>
          ))}
      </div>

      <div className="mt-4 flex-1 overflow-y-auto px-2 py-2">
        <p className="px-2.5 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-600">
          Sessions
        </p>
        {sessions.length === 0 && (
          <p className="px-2.5 py-3 text-[12px] text-zinc-600">No sessions yet -- start one above.</p>
        )}
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
                  <span className="shrink-0">{timeAgo(s.updatedAt)}</span>
                  {s.unread && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent-to" />}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-white/[0.06] p-3">
        <button
          onClick={onLogout}
          title="Sign out"
          className="group flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-white/[0.03]"
        >
          {user.avatar_url ? (
            <img
              src={user.avatar_url}
              alt=""
              className="h-7 w-7 shrink-0 rounded-full ring-1 ring-white/10"
            />
          ) : (
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-zinc-700 to-zinc-800 text-[11px] font-semibold text-zinc-200 ring-1 ring-white/10">
              {initials}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium text-zinc-200">{user.github_login}</p>
            <p className="truncate text-[11px] text-zinc-500">{user.email ?? "no public email"}</p>
          </div>
          <LogOut size={15} className="shrink-0 text-zinc-600 group-hover:text-zinc-300" />
        </button>
      </div>
        </>
      )}
    </aside>
  );
}
