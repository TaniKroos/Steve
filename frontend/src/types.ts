// Mirrors backend/app/schemas/auth.py's UserResponse -- field names match
// the JSON backend actually sends (snake_case), not renamed to camelCase,
// so there's no silent mapping layer to keep in sync by hand.
export interface CurrentUser {
  id: string;
  github_login: string;
  email: string | null;
  avatar_url: string | null;
}

// Mirrors backend/app/schemas/github.py's RepoResponse -- the real,
// backend-connected repo shape. Kept separate from `Repo` below (which
// is the mock-data shape used by the still-unwired session UI) so the
// two don't get silently conflated while only part of the app is real.
export interface GithubRepo {
  id: string;
  owner: string;
  name: string;
  default_branch: string;
  private: boolean;
}

// Mirrors backend/app/schemas/session.py's SessionResponse. Kept
// separate from `Session` below (the mock-data shape the still-unwired
// chat UI uses) for the same reason as `GithubRepo` vs `Repo` above.
export interface RemoteSession {
  id: string;
  repo_id: string;
  status: string;
  title: string | null;
  base_branch: string | null;
  branch_name: string | null;
  pr_number: number | null;
  pr_url: string | null;
  created_at: string;
  last_active_at: string;
}

// Backend's real status vocabulary (cloudagent_core/db/models.py's
// comment on Session.status), used directly rather than mapped through a
// separate mock-UI vocabulary -- "blocked" *is* "awaiting the user", not
// a distinct concept that needs translating.
export type SessionStatus = "starting" | "running" | "blocked" | "idle" | "failed";

export interface Repo {
  id: string;
  owner: string;
  name: string;
  defaultBranch: string;
  private: boolean;
}

export interface Session {
  id: string;
  title: string;
  repo: Repo;
  branchName: string;
  status: SessionStatus;
  prNumber?: number;
  prUrl?: string;
  updatedAt: string;
  unread?: boolean;
}

// Deliberately `string`, not a closed union -- agent_loop's ToolRegistry
// (agent_loop/app/tools/registry.py) can grow new tools without a
// frontend type change; ToolCallCard's TOOL_META has a generic fallback
// for any name it doesn't specifically recognize.
export type ToolName = string;

export type ToolCallStatus = "running" | "success" | "error";

export interface ToolCall {
  id: string;
  tool: ToolName;
  status: ToolCallStatus;
  summary: string;
  detail?: string;
  meta?: string;
}

export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text?: string;
  toolCalls?: ToolCall[];
  createdAt: string;
  streaming?: boolean;
}

// --- Real wire shapes from backend (mirrors backend/app/schemas/session.py) ---

export interface RemoteToolCall {
  id: string;
  tool_name: string;
  tool_use_id: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  status: "pending" | "success" | "error";
}

// One provider-neutral content block -- mirrors the comment on
// Message.content in cloudagent_core/db/models.py exactly. A `Message`'s
// `content` is a list of these, never a single vendor's raw wire format.
export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean };

export interface RemoteMessage {
  id: string;
  role: string;
  content: ContentBlock[];
  sequence_no: number;
  created_at: string;
  tool_calls: RemoteToolCall[];
}

// --- Live SSE event shapes (agent_loop/app/events/publisher.py) ---

export type SessionEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_call"; id: string; tool: string; status: ToolCallStatus; summary: string }
  | { type: "shell_output"; shell_id: string | null; stream: "stdout" | "stderr"; chunk: string }
  | { type: "file_edit"; path: string; tool: string }
  | { type: "file_removed"; path: string }
  | { type: "message_complete" }
  | { type: "status"; text: string }
  | { type: "session_status"; status: SessionStatus }
  | { type: "done"; pr_url: string; pr_number: number };

// --- Live workspace view (claude/live-workspace-view-plan.md) ---

// One `file_edit` SSE event, kept around client-side as a lightweight
// activity log -- never carries diff content itself (see the plan's §3.1
// for why that's pulled on demand instead), just enough to badge the
// file tree and know which open file to silently refresh.
export interface FileEditEntry {
  path: string;
  tool: string;
  at: number; // Date.now(), for React state identity / ordering only
}

// One `file_removed` SSE event -- deletions detected by the
// git-status-diff sync (claude/live-workspace-v2.md §3), never by a
// tracked editor tool (there is no delete_file tool -- see
// claude/architecture-plan.md §4).
export interface FileRemovedEntry {
  path: string;
  at: number;
}

// One `shell_output` chunk, kept for the Terminal tab's own buffer --
// separate from ToolCallCard's accumulation of the same events into a
// chat bubble's `detail` string; this is the raw, whole-session-so-far
// stream the Terminal tab renders.
export interface TerminalLine {
  stream: "stdout" | "stderr";
  chunk: string;
}
