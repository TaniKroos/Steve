import type { ChatMessage } from "../types";
import { Sparkles } from "lucide-react";
import { ToolCallCard } from "./ToolCallCard";

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-accent-via to-accent-to px-4 py-2.5 text-[13.5px] leading-relaxed text-white shadow-[0_4px_20px_-8px_rgba(47,111,237,0.4)]">
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-white/[0.09] to-white/[0.02] ring-1 ring-white/[0.06]">
        <Sparkles size={13} className="text-accent-to" />
      </div>
      <div className="flex min-w-0 max-w-[80%] flex-1 flex-col gap-2.5">
        {message.text && (
          <p className="text-[13.5px] leading-relaxed text-zinc-200">
            {message.text}
            {message.streaming && (
              <span className="ml-0.5 inline-block h-[14px] w-[2px] translate-y-[2px] animate-blink bg-accent-to align-middle" />
            )}
          </p>
        )}
        {message.toolCalls?.map((call) => (
          <ToolCallCard key={call.id} call={call} />
        ))}
      </div>
    </div>
  );
}
