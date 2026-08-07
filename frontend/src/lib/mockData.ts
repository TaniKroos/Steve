import type { ChatMessage, Session } from "../types";

const repoWebsite = {
  id: "r1",
  owner: "tanishsaini",
  name: "orbit-website",
  defaultBranch: "main",
  private: false,
};

const repoApi = {
  id: "r2",
  owner: "tanishsaini",
  name: "billing-service",
  defaultBranch: "main",
  private: true,
};

const repoMobile = {
  id: "r3",
  owner: "tanishsaini",
  name: "orbit-mobile",
  defaultBranch: "develop",
  private: true,
};

export const sessions: Session[] = [
  {
    id: "s1",
    title: "Fix flaky checkout retry test",
    repo: repoApi,
    branchName: "agent/fix-checkout-retry",
    status: "running",
    updatedAt: "2m ago",
  },
  {
    id: "s2",
    title: "Add dark mode to settings page",
    repo: repoWebsite,
    branchName: "agent/settings-dark-mode",
    status: "awaiting_user",
    updatedAt: "14m ago",
    unread: true,
  },
  {
    id: "s3",
    title: "Migrate auth store to Zustand",
    repo: repoMobile,
    branchName: "agent/zustand-auth",
    status: "done",
    prNumber: 482,
    prUrl: "https://github.com/tanishsaini/orbit-mobile/pull/482",
    updatedAt: "1h ago",
  },
  {
    id: "s4",
    title: "Speed up image pipeline on upload",
    repo: repoWebsite,
    branchName: "agent/image-pipeline-perf",
    status: "idle",
    updatedAt: "5h ago",
  },
  {
    id: "s5",
    title: "Write tests for invoice PDF export",
    repo: repoApi,
    branchName: "agent/invoice-pdf-tests",
    status: "done",
    prNumber: 471,
    prUrl: "https://github.com/tanishsaini/billing-service/pull/471",
    updatedAt: "1d ago",
  },
];

export const activeSessionId = "s1";

export const messagesBySession: Record<string, ChatMessage[]> = {
  s1: [
    {
      id: "m1",
      role: "user",
      text: "The checkout_retry test in tests/checkout/test_retry.py is flaky in CI — fails maybe 1 in 5 runs. Can you dig in and fix it?",
      createdAt: "10:41 AM",
    },
    {
      id: "m2",
      role: "assistant",
      text: "Sure, let me pull up the test and the retry logic it's exercising.",
      createdAt: "10:41 AM",
      toolCalls: [
        {
          id: "t1",
          tool: "shell_exec",
          status: "success",
          summary: "pytest tests/checkout/test_retry.py -q --count=20",
          detail:
            "Ran 20x  →  17 passed, 3 failed\nFAILED tests/checkout/test_retry.py::test_retry_backoff_order - AssertionError: attempts out of order",
          meta: "exit 1 · 4.2s",
        },
      ],
    },
    {
      id: "m3",
      role: "assistant",
      text: "Found it — the retry queue uses a plain list and sorts by a timestamp that's captured with `time.time()`, which has low resolution on the CI runners. Two retries can get identical timestamps and then sort non-deterministically. Opening the retry module.",
      createdAt: "10:42 AM",
      toolCalls: [
        {
          id: "t2",
          tool: "open_file",
          status: "success",
          summary: "checkout/retry_queue.py",
          detail: "Lines 1–48 shown — sort_key = lambda r: r.queued_at",
          meta: "48 lines",
        },
      ],
    },
    {
      id: "m4",
      role: "assistant",
      text: "Replacing the timestamp-based sort key with a monotonic sequence counter so ordering is stable regardless of clock resolution.",
      createdAt: "10:43 AM",
      toolCalls: [
        {
          id: "t3",
          tool: "str_replace",
          status: "success",
          summary: "checkout/retry_queue.py",
          detail:
            "-        self.queued_at = time.time()\n+        self.queued_at = time.time()\n+        self.sequence = next(_sequence_counter)\n\n-        return sorted(self._items, key=lambda r: r.queued_at)\n+        return sorted(self._items, key=lambda r: (r.queued_at, r.sequence))",
          meta: "2 hunks",
        },
        {
          id: "t4",
          tool: "shell_exec",
          status: "success",
          summary: "pytest tests/checkout/test_retry.py -q --count=50",
          detail: "Ran 50x  →  50 passed",
          meta: "exit 0 · 9.8s",
        },
      ],
    },
    {
      id: "m5",
      role: "assistant",
      text: "50/50 runs green. Pushing the fix to the branch now and I'll open a PR once it's up.",
      createdAt: "10:44 AM",
      streaming: true,
      toolCalls: [
        {
          id: "t5",
          tool: "shell_exec",
          status: "running",
          summary: "git push origin agent/fix-checkout-retry",
          meta: "running…",
        },
      ],
    },
  ],
  s2: [
    {
      id: "m1",
      role: "user",
      text: "Add a dark mode toggle to the settings page, should persist to localStorage.",
      createdAt: "9:58 AM",
    },
    {
      id: "m2",
      role: "assistant",
      text: "On it. I've wired up a theme context and a toggle in Settings — before I touch the design tokens across the rest of the app, quick check: should dark mode be system-aware by default, or opt-in only?",
      createdAt: "10:03 AM",
      toolCalls: [
        {
          id: "t1",
          tool: "create_file",
          status: "success",
          summary: "src/theme/ThemeProvider.tsx",
          meta: "62 lines",
        },
      ],
    },
  ],
  s3: [
    {
      id: "m1",
      role: "user",
      text: "Migrate the auth store from Context+useReducer to Zustand, keep the public hook API identical.",
      createdAt: "Yesterday",
    },
    {
      id: "m2",
      role: "assistant",
      text: "Done — migrated useAuthStore to Zustand with the same selectors, all 34 call sites still compile, and the PR is up.",
      createdAt: "Yesterday",
      toolCalls: [
        {
          id: "t1",
          tool: "git_create_pr",
          status: "success",
          summary: "Migrate auth store to Zustand",
          detail: "#482 opened against main",
          meta: "orbit-mobile",
        },
      ],
    },
  ],
};
