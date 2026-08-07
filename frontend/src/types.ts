// Mirrors backend/app/schemas/auth.py's UserResponse -- field names match
// the JSON backend actually sends (snake_case), not renamed to camelCase,
// so there's no silent mapping layer to keep in sync by hand.
export interface CurrentUser {
  id: string;
  github_login: string;
  email: string | null;
  avatar_url: string | null;
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
