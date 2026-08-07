import { Sparkles } from "lucide-react";

export function Logo({ withWordmark = true, size = "md" }: { withWordmark?: boolean; size?: "sm" | "md" | "lg" }) {
  const boxSize = size === "lg" ? "h-11 w-11" : size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const iconSize = size === "lg" ? 20 : size === "sm" ? 13 : 16;
  const textSize = size === "lg" ? "text-xl" : size === "sm" ? "text-sm" : "text-base";

  return (
    <div className="flex items-center gap-2.5">
      <div
        className={`${boxSize} shrink-0 rounded-[10px] bg-gradient-to-br from-accent-from via-accent-via to-accent-to flex items-center justify-center glow-ring`}
      >
        <Sparkles size={iconSize} strokeWidth={2.25} className="text-white drop-shadow" />
      </div>
      {withWordmark && (
        <span className={`${textSize} font-semibold tracking-tight text-gradient`}>Steve</span>
      )}
    </div>
  );
}
