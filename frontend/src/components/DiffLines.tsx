// Shared +/- line coloring -- factored out of ToolCallCard.tsx's
// original inline `DetailLine`, per claude/live-workspace-view-plan.md
// §3.3: reused here for chat tool-call detail *and* the Files tab's
// live/cumulative diff views, so both render the same diff the same way.
export function DiffLines({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => (
        <DiffLine key={i} line={line} />
      ))}
    </>
  );
}

function DiffLine({ line }: { line: string }) {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return <div className="bg-white/[0.08] px-3 text-zinc-200">{line}</div>;
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return <div className="bg-zinc-500/[0.12] px-3 text-zinc-300">{line}</div>;
  }
  if (line.startsWith("FAILED") || line.toLowerCase().includes("error")) {
    return <div className="px-3 text-zinc-300">{line}</div>;
  }
  return <div className="px-3 text-zinc-400">{line || " "}</div>;
}
