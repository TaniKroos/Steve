import { useState } from "react";
import { ArrowUp, GitBranch, Loader2, X } from "lucide-react";
import { ApiError, api } from "../lib/api";
import type { GithubRepo, RemoteSession } from "../types";

export function NewSessionModal({
  repos,
  onClose,
  onCreated,
}: {
  repos: GithubRepo[];
  onClose: () => void;
  // Passes the typed instruction back too -- `Session.title` has no
  // writer yet server-side (a known, pre-existing gap), so the caller
  // uses this to show something better than a placeholder immediately.
  onCreated: (session: RemoteSession, initialMessage: string) => void;
}) {
  const [repoId, setRepoId] = useState(repos[0]?.id ?? "");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!repoId || !message.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const trimmed = message.trim();
      const session = await api.createSession(repoId, trimmed);
      onCreated(session, trimmed);
    } catch (err) {
      // A 503 here means Agent Loop rejected or couldn't be reached for
      // the hand-off -- see SessionService.create_session and
      // app/exceptions.py's AgentLoopUnavailable. The sandbox that was
      // provisioned for this attempt is already torn down by the time
      // this error surfaces, so this isn't a leak -- just a failed attempt.
      if (err instanceof ApiError && err.status === 503) {
        setError("Agent Loop couldn't start this session (it may be down, or hit an error connecting to the sandbox). The sandbox was cleaned up automatically -- check agent_loop's logs and try again.");
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="glow-ring w-full max-w-lg rounded-2xl border border-white/[0.08] bg-surface p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-100">New session</h2>
          <button
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-500 transition hover:bg-white/[0.06] hover:text-zinc-300"
          >
            <X size={15} />
          </button>
        </div>

        {repos.length === 0 ? (
          <p className="rounded-lg border border-dashed border-white/[0.08] px-3 py-4 text-center text-[13px] text-zinc-500">
            Connect a repo first -- see the sidebar.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-zinc-600">
                Repository
              </label>
              <div className="flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.02] px-2.5 py-2">
                <GitBranch size={14} className="shrink-0 text-zinc-600" />
                <select
                  value={repoId}
                  onChange={(e) => setRepoId(e.target.value)}
                  disabled={submitting}
                  className="w-full bg-transparent text-[13px] text-zinc-200 focus:outline-none"
                >
                  {repos.map((r) => (
                    <option key={r.id} value={r.id} className="bg-surface">
                      {r.owner}/{r.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-zinc-600">
                What should the agent do?
              </label>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                disabled={submitting}
                rows={3}
                placeholder="Fix the failing test in..., add a health check endpoint, ..."
                className="w-full resize-none rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-[13px] text-zinc-100 placeholder:text-zinc-600 focus:border-white/[0.16] focus:outline-none"
              />
            </div>

            {error && (
              <p className="rounded-lg border border-white/15 bg-white/[0.06] px-3 py-2.5 text-[12.5px] leading-relaxed text-zinc-200">
                {error}
              </p>
            )}

            <button
              onClick={handleSubmit}
              disabled={submitting || !repoId || !message.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-zinc-200 py-2.5 text-sm font-semibold text-zinc-900 transition disabled:opacity-40 enabled:hover:scale-[1.01] enabled:hover:bg-zinc-100"
            >
              {submitting ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <ArrowUp size={16} strokeWidth={2.5} />
              )}
              Start session
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
