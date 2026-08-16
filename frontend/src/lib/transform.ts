// Pure functions turning backend's real wire shapes (types.ts's `Remote*`
// types) into the shapes the existing (originally mock-data-driven) chat
// UI components already render. Kept in one file so the two "language"
// on either side of the API boundary -- backend's DB-shaped rows vs. the
// UI's chat-bubble shape -- never leak into each other inside a component.

import type {
  ChatMessage,
  ContentBlock,
  GithubRepo,
  RemoteMessage,
  RemoteSession,
  RemoteToolCall,
  Repo,
  Session,
  SessionStatus,
  ToolCall,
} from "../types";

export function toUiRepo(repo: GithubRepo): Repo {
  return { id: repo.id, owner: repo.owner, name: repo.name, defaultBranch: repo.default_branch, private: repo.private };
}

const FALLBACK_REPO: Repo = { id: "unknown", owner: "unknown", name: "unknown", defaultBranch: "main", private: false };

export function toUiSession(remote: RemoteSession, repos: GithubRepo[], fallbackTitle?: string): Session {
  const repo = repos.find((r) => r.id === remote.repo_id);
  return {
    id: remote.id,
    // `title` is never actually written anywhere yet (backend's
    // `sessions.title` column has no writer -- a known, pre-existing
    // gap, not something this pass adds) -- `fallbackTitle` lets a
    // freshly-created session show its own initial instruction
    // immediately instead of a placeholder, since NewSessionModal
    // already has that text on hand at creation time.
    title: remote.title ?? fallbackTitle ?? (repo ? `${repo.owner}/${repo.name}` : "Session"),
    repo: repo ? toUiRepo(repo) : FALLBACK_REPO,
    branchName: remote.branch_name ?? "",
    status: remote.status as SessionStatus,
    prNumber: remote.pr_number ?? undefined,
    prUrl: remote.pr_url ?? undefined,
    updatedAt: remote.last_active_at,
  };
}

function joinText(blocks: ContentBlock[]): string {
  return blocks
    .filter((b): b is Extract<ContentBlock, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("");
}

function toUiToolCall(block: Extract<ContentBlock, { type: "tool_use" }>, row: RemoteToolCall | undefined): ToolCall {
  const status: ToolCall["status"] = !row || row.status === "pending" ? "running" : row.status === "error" ? "error" : "success";
  const { summary, detail, meta } = summarizeTool(block.name, block.input, row?.output ?? null);
  return { id: block.id, tool: block.name, status, summary, detail, meta };
}

// Agent Loop's own tool implementations (agent_loop/app/tools/*.py) get
// the final say on exactly what `input`/`output` contain -- this is a
// best-effort human-readable rendering of the ones most likely to show
// up early, with a generic JSON fallback for everything else rather than
// a hard requirement to keep this in lockstep with every tool.
function summarizeTool(
  name: string,
  input: Record<string, unknown>,
  output: Record<string, unknown> | null,
): { summary: string; detail?: string; meta?: string } {
  const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
  switch (name) {
    case "shell_exec": {
      const stdout = str(output?.stdout) ?? "";
      const stderr = str(output?.stderr) ?? "";
      const exitCode = output?.exit_code;
      return {
        summary: str(input.command) ?? "",
        detail: stdout || stderr ? `stdout:\n${stdout}\nstderr:\n${stderr}` : undefined,
        meta: typeof exitCode === "number" ? `exit ${exitCode}` : undefined,
      };
    }
    case "open_file":
    case "str_replace":
    case "create_file":
    case "insert_at_line":
    case "undo_edit":
      return { summary: str(input.path) ?? "", detail: str(output?.content) };
    case "shell_view":
    case "shell_write":
    case "shell_kill":
      return { summary: str(input.shell_id) ?? "", detail: str(output?.content) };
    case "git_create_pr":
      return { summary: str(input.title) ?? "opened a pull request", detail: str(output?.pr_url) };
    case "git_update_pr_description":
      return { summary: "update pull request description", detail: str(input.body) };
    case "git_view_pr":
    case "git_pr_checks":
      return { summary: `#${input.number ?? "?"}`, detail: output ? JSON.stringify(output, null, 2) : undefined };
    case "message_user":
      return { summary: str(input.text) ?? "" };
    case "wait":
      return { summary: `wait ${input.seconds ?? "?"}s` };
    case "list_secrets":
      return { summary: "list secrets", detail: str(output?.content) };
    default:
      return {
        summary: JSON.stringify(input),
        detail: output ? JSON.stringify(output, null, 2) : undefined,
      };
  }
}

// `RemoteMessage`s whose content is *only* `tool_result` blocks are
// internal plumbing (SessionWorker feeding a tool's result back to the
// model, see agent_loop/app/loop/session_worker.py's history-append
// pattern) -- not something a person typed or should see as a chat
// bubble. Everything else renders.
function isInternalPlumbing(blocks: ContentBlock[]): boolean {
  return blocks.length > 0 && blocks.every((b) => b.type === "tool_result");
}

export function toChatMessages(messages: RemoteMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    if (isInternalPlumbing(m.content)) continue;

    const text = joinText(m.content);
    const toolCalls = m.content
      .filter((b): b is Extract<ContentBlock, { type: "tool_use" }> => b.type === "tool_use")
      .map((block) => toUiToolCall(block, m.tool_calls.find((tc) => tc.tool_use_id === block.id)));

    out.push({
      id: m.id,
      role: m.role === "user" ? "user" : "assistant",
      text: text || undefined,
      toolCalls: toolCalls.length ? toolCalls : undefined,
      createdAt: m.created_at,
    });
  }
  return out;
}
