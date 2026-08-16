// Owns everything about "the currently open session's conversation":
// loading its real history (GET .../messages), opening a live SSE
// connection for whatever's still in flight (GET .../events), merging
// the two into one message list, and sending follow-up replies. Kept as
// one hook (not split into a separate "just the SSE plumbing" hook)
// because the two are never used independently -- the live event stream
// only makes sense layered on top of a loaded history.
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { toChatMessages } from "./transform";
import type { ChatMessage, SessionEvent, ToolCall } from "../types";

export function useSessionChat(sessionId: string | null, onSessionUpdate: (prUrl: string, prNumber: number) => void) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<ChatMessage | null>(null);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // `onSessionUpdate` is recreated every AppShell render -- routing it
  // through a ref keeps the SSE effect below keyed only on `sessionId`,
  // not on the caller's render cycle (which would otherwise tear down
  // and reopen the connection far more often than the session actually changes).
  const onSessionUpdateRef = useRef(onSessionUpdate);
  onSessionUpdateRef.current = onSessionUpdate;

  // Load real history whenever the selected session changes. Any
  // in-progress draft/live-status from the *previous* session gets
  // dropped here too -- it belongs to a connection that's about to close.
  useEffect(() => {
    setDraft(null);
    setLiveStatus(null);
    if (!sessionId) {
      setHistory([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .messages(sessionId)
      .then((remote) => {
        if (!cancelled) setHistory(toChatMessages(remote));
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // The live SSE connection. Reopened only when `sessionId` itself
  // changes -- see the ref above for why the handler closures don't
  // need to be in the dependency list.
  useEffect(() => {
    if (!sessionId) return;

    const source = new EventSource(api.eventsUrl(sessionId), { withCredentials: true });

    const emptyDraft = (): ChatMessage => ({
      id: `draft-${Date.now()}`,
      role: "assistant",
      createdAt: new Date().toISOString(),
      streaming: true,
    });

    source.onmessage = (raw) => {
      let event: SessionEvent;
      try {
        event = JSON.parse(raw.data);
      } catch {
        return; // malformed payload -- ignore rather than crash the connection
      }

      switch (event.type) {
        case "text_delta":
          setDraft((current) => {
            const base = current ?? emptyDraft();
            return { ...base, text: (base.text ?? "") + event.text };
          });
          break;

        case "tool_call":
          setDraft((current) => {
            const base = current ?? emptyDraft();
            const toolCalls: ToolCall[] = [...(base.toolCalls ?? [])];
            const existing = toolCalls.findIndex((tc) => tc.id === event.id);
            const updated: ToolCall = {
              id: event.id,
              tool: event.tool,
              status: event.status,
              summary: event.summary,
              detail: existing >= 0 ? toolCalls[existing].detail : undefined,
            };
            if (existing >= 0) toolCalls[existing] = updated;
            else toolCalls.push(updated);
            return { ...base, toolCalls };
          });
          break;

        case "shell_output":
          // Best-effort: append into whichever tool call is currently
          // last in the draft's list. Correct as long as tool dispatch
          // is sequential (it is -- see SessionWorker._dispatch_all),
          // so there's never more than one "running" shell at a time to
          // disambiguate against by shell_id.
          setDraft((current) => {
            if (!current?.toolCalls?.length) return current;
            const toolCalls = [...current.toolCalls];
            const last = toolCalls[toolCalls.length - 1];
            toolCalls[toolCalls.length - 1] = { ...last, detail: (last.detail ?? "") + event.chunk };
            return { ...current, toolCalls };
          });
          break;

        case "message_complete":
          setDraft((current) => {
            if (current) setHistory((h) => [...h, { ...current, streaming: false }]);
            return null;
          });
          break;

        case "status":
          setLiveStatus(event.text);
          break;

        case "done":
          setLiveStatus(null);
          onSessionUpdateRef.current(event.pr_url, event.pr_number);
          break;
      }
    };

    // EventSource retries transient drops on its own; nothing to do here
    // beyond letting the browser handle it -- explicit close (below) is
    // what actually ends the connection for good.
    source.onerror = () => {};

    return () => source.close();
  }, [sessionId]);

  const send = async (text: string) => {
    if (!sessionId) return;
    // Optimistic append -- the real row will also come back through
    // history on the next session load, but showing it immediately
    // means the composer doesn't feel like it swallowed the message.
    setHistory((h) => [
      ...h,
      { id: `local-${Date.now()}`, role: "user", text, createdAt: new Date().toISOString() },
    ]);
    await api.sendMessage(sessionId, text);
  };

  return {
    messages: draft ? [...history, draft] : history,
    liveStatus,
    loading,
    send,
  };
}
