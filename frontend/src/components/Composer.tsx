import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Paperclip } from "lucide-react";
import { ApiError } from "../lib/api";

// Keep this in sync with the textarea's `max-h-40` class below -- the
// class is the hard CSS ceiling (also what makes `overflow-y-auto` start
// actually scrolling instead of clipping), this is what the auto-grow
// effect caps itself at so it never fights that ceiling.
const MAX_TEXTAREA_HEIGHT_PX = 160;

export function Composer({
  disabled,
  placeholder,
  onSend,
}: {
  disabled?: boolean;
  placeholder?: string;
  onSend: (text: string) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // A plain `rows={1}` textarea never grows on its own -- content past
  // the first line just scrolls inside a fixed-height box. Measuring
  // `scrollHeight` after resetting to "auto" is the standard way to get
  // the browser to tell us how tall the content actually wants to be,
  // then clamping it is what turns "grows a bit, then scrolls" into
  // reality instead of "always one line" or "grows forever."
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT_PX)}px`;
  }, [value]);

  const submit = async () => {
    const text = value.trim();
    if (!text || disabled || sending) return;
    setValue("");
    setSending(true);
    setError(null);
    try {
      await onSend(text);
    } catch (err) {
      // Previously uncaught entirely -- the message was already cleared
      // above with zero feedback on failure, silently swallowed as an
      // unhandled rejection. Restoring the text means a failed send
      // isn't just gone; the specific 409 case below is a real, expected
      // outcome now (a resume attempt on a session whose PR already
      // merged -- claude/session-resume-plan.md), not just a fallback
      // for unexpected errors.
      setValue(text);
      if (err instanceof ApiError && err.status === 409) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Failed to send -- try again.");
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="px-6 pb-5 pt-3">
      <div className="mx-auto max-w-3xl">
        <div className="glow-ring flex items-end gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-2 pl-3.5 transition focus-within:border-white/[0.16]">
          <button className="mb-1.5 shrink-0 text-zinc-500 transition hover:text-zinc-300">
            <Paperclip size={17} />
          </button>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            rows={1}
            placeholder={placeholder ?? "Ask the agent to fix, build, or ship something…"}
            disabled={disabled}
            className="max-h-40 min-h-[26px] flex-1 resize-none overflow-y-auto bg-transparent py-1.5 text-[13.5px] text-zinc-100 placeholder:text-zinc-600 focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={() => void submit()}
            disabled={!value.trim() || disabled || sending}
            className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-zinc-200 text-zinc-900 transition disabled:opacity-30 disabled:grayscale enabled:hover:scale-105 enabled:hover:bg-zinc-100"
          >
            {sending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} strokeWidth={2.5} />}
          </button>
        </div>
        {error ? (
          <p className="mt-2 rounded-lg border border-white/15 bg-white/[0.06] px-3 py-2 text-center text-[12px] leading-relaxed text-zinc-200">
            {error}
          </p>
        ) : (
          <p className="mt-2 text-center text-[11px] text-zinc-600">
            Agent runs inside an isolated sandbox with repo-scoped GitHub access · nothing is pushed without opening a PR
          </p>
        )}
      </div>
    </div>
  );
}
