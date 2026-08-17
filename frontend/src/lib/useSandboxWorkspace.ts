// Live workspace view (claude/live-workspace-view-plan.md) -- the
// pull-only half of the panel's data. Deliberately separate from
// useSessionChat.ts (which owns the SSE connection itself): this hook
// only ever reacts to `lastFileEdit` as a plain input value, it never
// opens its own EventSource (plan §6 -- one connection, shared).
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { FileEditEntry } from "../types";

export function useSandboxWorkspace(
  sessionId: string | null,
  sessionStatus: string | undefined,
  lastFileEdit: FileEditEntry | null,
) {
  const [files, setFiles] = useState<string[]>([]);
  // True on a 409 (SessionNotActive -- no live Agent Loop owner to ask
  // right now) or any other fetch failure; the panel shows "not
  // available" rather than an empty tree or a crash. Full UX for this
  // state is deliberately deferred (plan §7) -- this is just the signal.
  const [filesUnavailable, setFilesUnavailable] = useState(false);

  const [openPath, setOpenPath] = useState<string | null>(null);
  const [openContent, setOpenContent] = useState<string | null>(null);
  const [openDiff, setOpenDiff] = useState<string | null>(null);
  const [openLoading, setOpenLoading] = useState(false);

  // The full diff vs. the default branch -- the confirm-before-PR
  // review view (plan §4/§7), pulled once whenever status flips to
  // `blocked`.
  const [reviewDiff, setReviewDiff] = useState<string | null>(null);

  useEffect(() => {
    setFiles([]);
    setFilesUnavailable(false);
    setOpenPath(null);
    setOpenContent(null);
    setOpenDiff(null);
    setReviewDiff(null);
  }, [sessionId]);

  // Fetch the tree once per session -- the panel's open by default
  // (AppShell.tsx's `sandboxOpen`), so there's no separate "panel just
  // opened" trigger to wait for. Exposed as `refresh` too, for the
  // Explorer header's manual refresh button -- a deliberate escape
  // hatch alongside the live create_file sync below, not a replacement
  // for it (e.g. a file deleted outside a tracked tool call, or a
  // reconnect after `filesUnavailable`, has no event to react to).
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

  const openFile = (path: string) => {
    if (!sessionId) return;
    setOpenPath(path);
    setOpenContent(null);
    setOpenDiff(null);
    setOpenLoading(true);
    Promise.all([api.fileContent(sessionId, path), api.fileDiff(sessionId, path)])
      .then(([content, diff]) => {
        setOpenContent(content.content);
        setOpenDiff(diff.diff);
      })
      .catch(() => {
        setOpenContent(null);
        setOpenDiff(null);
      })
      .finally(() => setOpenLoading(false));
  };

  // A `file_edit` notification arrived for the file the user already
  // has open -- silently re-pull just that file's content/diff. Never
  // fires for any other path: no auto-navigation, no background fetch
  // for files nobody's looking at (plan §3.1/§6).
  useEffect(() => {
    if (!sessionId || !openPath || !lastFileEdit || lastFileEdit.path !== openPath) return;
    Promise.all([api.fileContent(sessionId, openPath), api.fileDiff(sessionId, openPath)])
      .then(([content, diff]) => {
        setOpenContent(content.content);
        setOpenDiff(diff.diff);
      })
      .catch(() => {});
  }, [sessionId, openPath, lastFileEdit]);

  // Keep the Explorer tree in sync with files created mid-session,
  // without a full re-fetch. The tree itself was only ever fetched once
  // (the effect above), so a file created after that point silently
  // never showed up until the panel was closed and reopened, forcing a
  // remount and a fresh `listFiles` call -- that's what "have to
  // manually close it then open it to see current files" was. The
  // `file_edit` event already carries the exact path (session_worker.py
  // publishes it for every write tool, including create_file), so
  // appending it locally is enough -- no extra request needed. A no-op
  // for edits to files already in the tree.
  useEffect(() => {
    if (!lastFileEdit) return;
    setFiles((current) => (current.includes(lastFileEdit.path) ? current : [...current, lastFileEdit.path]));
  }, [lastFileEdit]);

  useEffect(() => {
    if (!sessionId || sessionStatus !== "blocked") return;
    api
      .cumulativeDiff(sessionId)
      .then((res) => setReviewDiff(res.diff))
      .catch(() => setReviewDiff(null));
  }, [sessionId, sessionStatus]);

  return { files, filesUnavailable, openPath, openContent, openDiff, openLoading, openFile, reviewDiff, refresh };
}
