// Live workspace view (claude/live-workspace-view-plan.md, extended by
// claude/live-workspace-v2.md) -- the pull-only half of the panel's
// data. Deliberately separate from useSessionChat.ts (which owns the
// SSE connection itself): this hook only ever reacts to `lastFileEdit`/
// `lastFileRemoved` as plain input values, it never opens its own
// EventSource (plan §6 -- one connection, shared).
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { FileEditEntry, FileRemovedEntry } from "../types";

interface OpenFileState {
  content: string | null;
  // The file's content as of HEAD -- the "before" half of Monaco's
  // DiffEditor (v2 plan §4.1), fetched alongside `content` so the
  // caller can decide "does this tab actually have uncommitted changes"
  // without a separate round trip.
  original: string | null;
  loading: boolean;
}

export function useSandboxWorkspace(
  sessionId: string | null,
  sessionStatus: string | undefined,
  lastFileEdit: FileEditEntry | null,
  lastFileRemoved: FileRemovedEntry | null,
) {
  const [files, setFiles] = useState<string[]>([]);
  // True on a 409 (SessionNotActive -- no live Agent Loop owner to ask
  // right now) or any other fetch failure; the panel shows "not
  // available" rather than an empty tree or a crash. Full UX for this
  // state is deliberately deferred (plan §7) -- this is just the signal.
  const [filesUnavailable, setFilesUnavailable] = useState(false);

  // Multi-tab editor state (v2 plan §4.1) -- `tabs` is every path opened
  // this session, in open order; `activePath` is whichever one is
  // currently shown; `cache` holds each tab's fetched content + HEAD
  // version keyed by path, so switching back to an already-visited tab
  // is instant instead of re-fetching (this is what "retained models"
  // meant in the plan -- the cache, not just Monaco's own internal one).
  const [tabs, setTabs] = useState<string[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, OpenFileState>>({});
  // A path that was just deleted while it was the tab on screen -- shown
  // as a transient notice instead of silently falling back to "select a
  // file", which would read as a bug rather than "this file is gone".
  // Cleared the moment another file is opened.
  const [deletedNotice, setDeletedNotice] = useState<string | null>(null);

  // The full diff vs. the default branch -- the confirm-before-PR
  // review view (plan §4/§7), pulled once whenever status flips to
  // `blocked`. Stays a unified-text view (DiffLines), not Monaco's
  // DiffEditor -- it's inherently a multi-file summary, which
  // DiffEditor (a single-file, two-blob comparison) isn't shaped for
  // (v2 plan §4.1).
  const [reviewDiff, setReviewDiff] = useState<string | null>(null);

  useEffect(() => {
    setFiles([]);
    setFilesUnavailable(false);
    setTabs([]);
    setActivePath(null);
    setCache({});
    setDeletedNotice(null);
    setReviewDiff(null);
  }, [sessionId]);

  // Fetch the tree once per session -- the panel's open by default
  // (AppShell.tsx's `sandboxOpen`), so there's no separate "panel just
  // opened" trigger to wait for. Exposed as `refresh` too, for the
  // Explorer header's manual refresh button -- a deliberate escape
  // hatch alongside the git-status-driven live sync (v2 plan §3), not a
  // replacement for it (e.g. a reconnect after `filesUnavailable`).
  const refresh = useCallback(() => {
    if (!sessionId) return;
    api
      .listFiles(sessionId)
      .then((paths) => {
        setFiles(paths);
        setFilesUnavailable(false);
      })
      .catch(() => setFilesUnavailable(true));
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    api
      .listFiles(sessionId)
      .then((paths) => {
        if (!cancelled) setFiles(paths);
      })
      .catch(() => {
        if (!cancelled) setFilesUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Keep the Explorer tree in sync with files created mid-session,
  // without a full re-fetch. The `file_edit` event already carries the
  // exact path, so appending it locally is enough -- a no-op for edits
  // to files already in the tree.
  useEffect(() => {
    if (!lastFileEdit) return;
    setFiles((current) => (current.includes(lastFileEdit.path) ? current : [...current, lastFileEdit.path]));
  }, [lastFileEdit]);

  // Opens a tab (or activates it if already open). Only marks it as
  // needing a fetch if it isn't cached yet -- the actual fetch happens
  // in the effect below, keyed off `activePath`, so re-activating an
  // already-cached tab never re-fetches.
  const openFile = useCallback((path: string) => {
    setDeletedNotice(null);
    setActivePath(path);
    setTabs((current) => (current.includes(path) ? current : [...current, path]));
    setCache((current) =>
      current[path] ? current : { ...current, [path]: { content: null, original: null, loading: true } },
    );
  }, []);

  const closeTab = useCallback((path: string) => {
    setTabs((current) => {
      const closedIndex = current.indexOf(path);
      const next = current.filter((p) => p !== path);
      setActivePath((active) => {
        if (active !== path) return active;
        // Activate whichever tab was immediately to the left, matching
        // the usual editor tab-close convention.
        return next[closedIndex - 1] ?? next[0] ?? null;
      });
      return next;
    });
  }, []);

  // The actual fetch -- runs only when the active tab's cache entry is
  // still sitting in the `loading: true` state `openFile`/the
  // invalidation effect below put it in.
  useEffect(() => {
    if (!sessionId || !activePath) return;
    const entry = cache[activePath];
    if (!entry || !entry.loading) return;
    let cancelled = false;
    Promise.all([api.fileContent(sessionId, activePath), api.fileOriginal(sessionId, activePath)])
      .then(([content, original]) => {
        if (cancelled) return;
        setCache((current) => ({
          ...current,
          [activePath]: { content: content.content, original: original.content, loading: false },
        }));
      })
      .catch(() => {
        if (cancelled) return;
        setCache((current) => ({ ...current, [activePath]: { content: null, original: null, loading: false } }));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, activePath, cache]);

  // A `file_edit` notification arrived for a path that's actually an
  // open tab -- invalidate its cache entry so it re-fetches (immediately
  // if it's the active tab, on next activation otherwise). Never fires
  // for a path that isn't an open tab: no background pre-fetching for
  // files nobody's looking at (plan §3.1/§6).
  useEffect(() => {
    if (!lastFileEdit || !tabs.includes(lastFileEdit.path)) return;
    setCache((current) => ({ ...current, [lastFileEdit.path]: { content: null, original: null, loading: true } }));
  }, [lastFileEdit, tabs]);

  // A `file_removed` notification (v2 plan §3's deletion handling,
  // detected by the git-status-diff sync, never by a tracked editor
  // tool) -- drop it from the tree, and if it was an open tab, close
  // that tab too.
  useEffect(() => {
    if (!lastFileRemoved) return;
    setFiles((current) => current.filter((p) => p !== lastFileRemoved.path));
    if (!tabs.includes(lastFileRemoved.path)) return;
    if (activePath === lastFileRemoved.path) setDeletedNotice(lastFileRemoved.path);
    closeTab(lastFileRemoved.path);
    setCache((current) => {
      if (!(lastFileRemoved.path in current)) return current;
      const next = { ...current };
      delete next[lastFileRemoved.path];
      return next;
    });
  }, [lastFileRemoved, tabs, activePath, closeTab]);

  useEffect(() => {
    if (!sessionId || sessionStatus !== "blocked") return;
    api
      .cumulativeDiff(sessionId)
      .then((res) => setReviewDiff(res.diff))
      .catch(() => setReviewDiff(null));
  }, [sessionId, sessionStatus]);

  const active = activePath ? cache[activePath] : undefined;

  return {
    files,
    filesUnavailable,
    refresh,
    tabs,
    activePath,
    activeContent: active?.content ?? null,
    activeOriginal: active?.original ?? null,
    activeLoading: active?.loading ?? false,
    deletedNotice,
    openFile,
    closeTab,
    reviewDiff,
  };
}
