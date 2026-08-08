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
  branch_name: string | null;
  pr_number: number | null;
  pr_url: string | null;
  created_at: string;
  last_active_at: string;
}

export type SessionStatus = "running" | "awaiting_user" | "idle" | "done" | "starting";

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

export type ToolName =
  | "shell_exec"
  | "open_file"
  | "str_replace"
  | "create_file"
  | "git_create_pr"
  | "git_view_pr";

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
