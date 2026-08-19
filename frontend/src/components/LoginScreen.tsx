import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, GitPullRequest, Sparkles, Terminal } from "lucide-react";
import { api } from "../lib/api";
import { GithubMark } from "./GithubMark";
import { Logo } from "./Logo";

// Keyed off the `?error=` code backend/app/routers/auth.py's /callback
// redirects back with on a failed login -- a top-level browser
// navigation, so it can't just return a JSON error body the way the
// SPA's own fetch calls do (see that file's docstring). Unknown/missing
// codes fall through to a generic message rather than showing nothing.
const ERROR_MESSAGES: Record<string, string> = {
  github_unavailable: "GitHub's API didn't respond just now. This is usually temporary -- try again in a moment.",
  invalid_state: "That login attempt expired or looked invalid. Please try again.",
};

const FEATURES = [
  {
    icon: Terminal,
    title: "Isolated cloud sandboxes",
    desc: "Every session gets its own microVM — shell, files, and git, cleanly separated from the agent loop.",
  },
  {
    icon: Sparkles,
    title: "Chat-driven, tool-calling agent",
    desc: "Describe the change. Watch it read code, edit files, and run commands in real time.",
  },
  {
    icon: GitPullRequest,
    title: "Ships as a real PR",
    desc: "Scoped GitHub App installation tokens — never a PAT — push a branch and open the PR for you.",
  },
];

export function LoginScreen() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const errorCode = searchParams.get("error");
  const errorMessage = errorCode ? (ERROR_MESSAGES[errorCode] ?? "Something went wrong signing you in. Please try again.") : null;

  useEffect(() => {
    // Covers landing back on "/" with an already-valid session cookie
    // (e.g. a bookmark, or a browser-back after logging in) -- skip
    // straight past the pitch page instead of asking for a redundant
    // login. A 401 here just means "not logged in," which is the
    // expected steady state for this page -- caught and discarded, not
    // left as an unhandled rejection. Skipped entirely when there's an
    // error to show -- a failed callback can still leave a stale-but-401
    // session state, and silently redirecting past the error the user
    // just landed here to see would be worse than the redundant check.
    if (errorCode) return;
    api
      .me()
      .then(() => navigate("/app", { replace: true }))
      .catch(() => {});
  }, [navigate, errorCode]);

  // Strip `?error=...` from the URL once it's been read into state, so
  // refreshing the page (or sharing the link) doesn't keep re-showing a
  // login failure that's already been dealt with.
  useEffect(() => {
    if (errorCode) setSearchParams({}, { replace: true });
  }, [errorCode, setSearchParams]);

  return (
    <div className="relative min-h-screen overflow-hidden bg-canvas">
      {/* ambient background */}
      <div className="pointer-events-none absolute inset-0 bg-grid [mask-image:radial-gradient(ellipse_70%_60%_at_50%_0%,black_10%,transparent_75%)]" />
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[560px] w-[860px] -translate-x-1/2 rounded-full bg-gradient-to-br from-white/15 via-zinc-700/10 to-transparent blur-3xl animate-pulse-slow" />
      <div className="pointer-events-none absolute bottom-[-12rem] right-[-8rem] h-[420px] w-[420px] rounded-full bg-zinc-700/15 blur-3xl" />

      <div className="relative z-10 flex min-h-screen flex-col">
        <header className="flex items-center justify-between px-6 py-6 sm:px-10">
          <Logo />
          <a
            href="#"
            className="text-sm text-zinc-400 transition hover:text-zinc-200"
            onClick={(e) => e.preventDefault()}
          >
            Docs
          </a>
        </header>

        <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col items-center justify-center px-6 pb-24 text-center">
          <div className="animate-fade-up mb-5 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs font-medium text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-zinc-200" />
            v1 · shell · editor · git
          </div>

          <h1 className="animate-fade-up [animation-delay:60ms] text-4xl font-bold tracking-tight text-zinc-400 sm:text-6xl">
            Your codebase,
            <br />
            <span className="text-gradient">worked on while you sleep.</span>
          </h1>

          <p className="animate-fade-up mt-6 max-w-xl text-balance text-base text-zinc-400 sm:text-lg [animation-delay:120ms]">
            Connect a repo, describe what needs to change, and let an agent fix it inside its own
            sandbox — then review the PR it opens for you.
          </p>

          {errorMessage && (
            <div className="animate-fade-up mt-6 flex max-w-md items-start gap-2.5 rounded-xl border border-white/15 bg-white/[0.06] px-4 py-3 text-left text-[13px] text-zinc-200">
              <AlertTriangle size={15} className="mt-0.5 shrink-0 text-zinc-300" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/*
            A real <a>, not an onClick + client-side navigate: this has
            to be a full page load. The browser needs to actually leave
            this SPA, go to GitHub, and come back -- see
            backend/app/routers/auth.py for the redirect chain this
            kicks off (/api/auth/login -> GitHub -> /api/auth/callback
            -> back to this app's root, now with a session cookie set).
          */}
          <a
            href={api.loginUrl}
            className="animate-fade-up group relative mt-10 inline-flex items-center gap-2.5 overflow-hidden rounded-xl bg-zinc-200 px-6 py-3.5 text-sm font-semibold text-zinc-900 shadow-[0_0_0_1px_rgba(255,255,255,0.08)] transition-[transform,background-color] duration-200 hover:scale-[1.02] hover:bg-zinc-100 active:scale-[0.98] [animation-delay:180ms]"
          >
            <GithubMark size={18} />
            Continue with GitHub
          </a>

          <p className="animate-fade-up mt-4 text-xs text-zinc-500 [animation-delay:220ms]">
            Installs as a GitHub App with scoped, short-lived tokens — no personal access tokens, ever.
          </p>

          <div className="animate-fade-up mt-20 grid w-full grid-cols-1 gap-4 text-left sm:grid-cols-3 [animation-delay:280ms]">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="glass glow-ring rounded-2xl border border-white/[0.06] p-5 transition hover:border-white/[0.12]"
              >
                <div className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-white/[0.08] to-white/[0.02] text-accent-to">
                  <f.icon size={17} strokeWidth={2} />
                </div>
                <h3 className="text-sm font-semibold text-zinc-100">{f.title}</h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-500">{f.desc}</p>
              </div>
            ))}
          </div>
        </main>
      </div>
    </div>
  );
}
