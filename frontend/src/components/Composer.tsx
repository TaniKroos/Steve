import { useState } from "react";
import { ArrowUp, Loader2, Paperclip } from "lucide-react";

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

  const submit = async () => {
    const text = value.trim();
    if (!text || disabled || sending) return;
    setValue("");
    setSending(true);
    try {
      await onSend(text);
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
            className="max-h-40 min-h-[26px] flex-1 resize-none bg-transparent py-1.5 text-[13.5px] text-zinc-100 placeholder:text-zinc-600 focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={() => void submit()}
            disabled={!value.trim() || disabled || sending}
            className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-zinc-200 text-zinc-900 transition disabled:opacity-30 disabled:grayscale enabled:hover:scale-105 enabled:hover:bg-zinc-100"
          >
            {sending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} strokeWidth={2.5} />}
          </button>
        </div>
        <p className="mt-2 text-center text-[11px] text-zinc-600">
          Agent runs inside an isolated sandbox with repo-scoped GitHub access · nothing is pushed without opening a PR
        </p>
      </div>
    </div>
  );
}
