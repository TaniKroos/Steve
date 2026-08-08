# CloudAgent — Requirements (v1)

This is the source of truth for *what* we're building, before diving into *how*. Everything here is derived from `Design/Design.md` and the decisions locked in during architecture planning (see `.claude/architecture-plan.md` and `.Arch/` for the how). If a requirement here ever conflicts with those docs, this file wins for scope questions — the others win for implementation detail.

**Legend:** `FR` = Functional Requirement (a thing the system does). `NFR` = Non-Functional Requirement (a quality the system must have while doing it).

---

## Functional Requirements

### Identity & GitHub connection

- **FR-1** — A user can sign in using **"Sign in with GitHub"**. GitHub is the sole login method for v1 — no email/password, no other OAuth providers.
- **FR-2** — On first login, the system creates a new user record keyed on the GitHub user ID. On every later login, it matches the existing record — a user never ends up with two accounts.
- **FR-3** — A logged-in user can connect one or more GitHub repositories by installing the CloudAgent GitHub App and choosing which repos to grant it access to.
- **FR-4** — After installing the App, the system lists which repos are now accessible and stores that list.
- **FR-5** — The system stays in sync if repo access changes outside the app (e.g. the user uninstalls the App or removes a repo from GitHub's side), via GitHub webhooks.

### Sessions (the core "chat with an agent" loop)

- **FR-6** — A user can start a new session against one connected repo, with an initial instruction ("fix the bug in X", "add a health check endpoint", etc.).
- **FR-7** — Starting a session provisions an isolated, per-session cloud sandbox — no two sessions ever share a sandbox.
- **FR-8** — Starting a session mints a short-lived, repo-scoped GitHub access token and hands it to the Agent Loop so it can clone/commit/push — this token is never shown to the frontend or the user.
- **FR-9** — A user can send follow-up messages into a session that's waiting on them (e.g. the agent asked a clarifying question).
- **FR-10** — A user can watch a session's progress live — assistant text and tool-call activity — while it runs, without refreshing the page.
- **FR-11** — A user can list their own past and active sessions, and open one to see its state.
- **FR-12** — A session that finishes its work results in a pushed branch and an opened pull request on GitHub (this is the Agent Loop's job — backend's part is just the initial handoff and later showing the PR link/status once Agent Loop is built).
- **FR-13** — Idle sessions and their sandboxes are automatically cleaned up after a timeout, so nothing runs (or costs money) forever unattended.

### Agent Loop

See `.claude/agent-loop-plan.md` for the full design behind these — this section is the "what," that doc is the "how."

- **FR-14** — Agent Loop clones the target repo into its sandbox using the handed-off installation token immediately on session start, and scrubs the token out of any persisted config (`.git/config`) right after — it never lingers on disk.
- **FR-15** — The agent has four tool groups available during a session: **Shell** (run commands), **Editor** (read/create/edit files), **User Interaction** (ask the user something, block/resume), and **Git/GitHub** (view/create/update PRs, check CI status) — matching `Design/Design.md`'s v1 tool scope exactly.
- **FR-16** — The agent can run against more than one LLM provider — Anthropic's Claude and a Llama-family model via an OpenAI-compatible endpoint — selected by configuration, with no branching on provider anywhere in the loop or tool logic itself.
- **FR-17** — When a tool call needs user input before continuing (`BLOCK`), the session pauses and resumes automatically the moment a follow-up message arrives (see FR-9), rather than ending or timing out.
- **FR-18** — Editor-tool edits (create / replace / insert / undo) happen against the sandbox's real filesystem API, never via shell text-manipulation commands (`sed`, sh `echo`, etc.), so every edit is precise and independently diffable.
- **FR-19** — On completing a task, the agent pushes a branch and opens a real GitHub pull request, and the session's `branch_name` / `pr_number` / `pr_url` reflect it — this is FR-12, made concrete.

### Out of scope for v1 (explicitly deferred, not forgotten)

- **FR-X1** — Direct live-terminal spectation (frontend connecting straight to the sandbox) — optional stretch, not required for the core product.
- **FR-X2** — LSP tooling, browser tooling, deployment tooling, MCP tool group — all explicitly post-v1 per `Design/Design.md`.
- **FR-X3** — Any login method other than GitHub.

---

## Non-Functional Requirements

### Security

- **NFR-1** — GitHub tokens (both the user's login token and any App installation token) are **never persisted to the database and never sent to the frontend**. They exist only in server-side process memory for as long as a single request or session needs them.
- **NFR-2** — The user's logged-in state is represented by our own signed, `httpOnly`, `SameSite` session cookie — never GitHub's token directly.
- **NFR-3** — Webhook payloads from GitHub are HMAC-signature-verified before being trusted.
- **NFR-4** — All service secrets (GitHub App private key, DB/Redis URLs, session-signing key, shared internal secret, etc.) live in environment variables / platform secrets — never committed to source.
- **NFR-5** — A global cap on concurrently active sandboxes exists as a cheap guard against runaway cost or abuse.

### Maintainability & code quality

- **NFR-6** — Code is organized in clear layers (HTTP → business logic → data access) so each class has one reason to change (Single Responsibility).
- **NFR-7** — Dependencies are passed into classes explicitly (constructor injection / FastAPI `Depends()`), not reached for globally — this is what makes unit testing possible without a live DB or network calls.
- **NFR-8** — New capability (a new tool, a new route, a new repo aggregate) should be addable by adding a new class, not editing unrelated existing ones (Open/Closed).
- **NFR-9** — Every file in this codebase carries comments explaining **what** a non-obvious block does and **why** it's written that way — this project doubles as a Python/backend learning exercise, so terse "clever" code without explanation is treated as a defect here, not a virtue.

### Scalability & reliability

- **NFR-10** — Scale target is **~50 concurrent users**, not enterprise scale — but this is a real project built to a good engineering standard, not a disposable demo cutting every corner that scale would technically allow. We still avoid infrastructure that's genuinely disproportionate to 50 users (multi-region, a full workflow-engine like Temporal) — but "small scale" is not blanket justification for skipping proper design (session ownership/routing, crash recovery, clean abstractions, tests). The bar is right-sized architecture, not minimum-effort architecture.
- **NFR-11** — Backend and Agent Loop are separate deployable services that can scale/restart independently of each other.
- **NFR-12** — A background sweep catches sandboxes/sessions that were never cleanly torn down (crashed worker, network blip) so nothing leaks resources indefinitely.

### Agent Loop

See `.claude/agent-loop-plan.md` for the full design. These exist because "small scale" was explicitly ruled out as an excuse to skip proper design here (NFR-10) — Agent Loop is the one part of this system with real distributed-systems shape (multiple instances, in-flight state, a swappable external dependency), so it gets its own explicit bar.

- **NFR-17** — Agent Loop can run as more than one instance simultaneously; a given session's live state belongs to exactly one instance at a time, tracked explicitly (a registry with a heartbeat) — never assumed via sticky load-balancer configuration that doesn't actually exist.
- **NFR-18** — If the instance holding a session dies, another instance detects it and resumes that session from persisted history within a bounded time (target: under a minute) — a session is never permanently stuck because one process crashed.
- **NFR-19** — The LLM provider lives behind one internal interface (Adapter pattern); adding or swapping a provider never requires changing `SessionWorker` or any tool's logic.
- **NFR-20** — Stored conversation history uses a provider-neutral internal format, not any single vendor's native wire format — so it isn't permanently coupled to whichever LLM provider was chosen first.
- **NFR-21** — A session's conversation history is kept in memory for the life of an active worker and only reloaded from the database on genuine crash-recovery — per-turn cost does not grow with how long a session has been running.
- **NFR-22** — Large tool-call outputs can be externalized to object storage past a size threshold without a future schema migration, even though that externalization isn't built until output sizes actually justify it.

### Developer experience

- **NFR-13** — Local development mirrors production topology closely enough to catch integration bugs early: Postgres + Redis run locally via Docker Compose, the same shared `cloudagent_core` package is used, the same async SQLAlchemy stack is used.
- **NFR-14** — A single shared package (`cloudagent_core`) is the one source of truth for the DB schema and GitHub App auth logic, installed into both `backend` and `agent_loop`, so those two services can never silently drift apart on what a `Session` row looks like or how a token gets minted.

### Observability

- **NFR-15** — Every tool call the agent makes is persisted (tool name, input, output, status, timing) — this is both a debugging aid and, later, an audit trail.
- **NFR-16** — Services expose a health-check endpoint suitable for platform-level liveness checks.

---

## How to use this file going forward

When a new feature is proposed, check here first — if it doesn't map to an FR, that's a signal to either add one deliberately (and confirm it's actually in scope for v1) or push it into "out of scope," not to build it quietly. When making an implementation trade-off, the NFRs above are the tie-breaker: e.g. "should this be a bit slower but easier to test" — NFR-7 says yes, favor testability at this project's scale.
