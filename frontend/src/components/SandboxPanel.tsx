import { useState } from "react";
import {
  Code2,
  File,
  FileJson,
  Folder,
  GitBranch,
  Globe,
  Lock,
  RefreshCw,
  Search,
  Settings2,
  X,
} from "lucide-react";
import type { Session } from "../types";

type Tab = "browser" | "vscode";

export function SandboxPanel({ session, onClose }: { session: Session; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("vscode");
  const live = session.status === "running" || session.status === "blocked";

  return (
    <aside className="flex h-screen w-[460px] shrink-0 flex-col border-l border-white/[0.06] bg-surface">
      <div className="flex items-center gap-1 border-b border-white/[0.06] px-3 py-2.5">
        <TabButton icon={Code2} label="VS Code" active={tab === "vscode"} onClick={() => setTab("vscode")} />
        <TabButton icon={Globe} label="Browser" active={tab === "browser"} onClick={() => setTab("browser")} />

        <div className="ml-auto flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10.5px] font-medium ${
              live ? "text-accent-to" : "text-zinc-500"
            }`}
          >
            <span className="relative flex h-1.5 w-1.5">
              {live && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-to opacity-60" />
              )}
              <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${live ? "bg-accent-to" : "bg-zinc-600"}`} />
            </span>
            {live ? "Live" : "Idle"}
          </span>
          <button onClick={onClose} className="text-zinc-500 transition hover:text-zinc-200">
            <X size={15} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1">{tab === "browser" ? <BrowserPreview /> : <VSCodePreview session={session} />}</div>
    </aside>
  );
}

function TabButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: typeof Globe;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium transition ${
        active ? "bg-white/[0.07] text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
      }`}
    >
      <Icon size={13} strokeWidth={2.25} />
      {label}
    </button>
  );
}

function BrowserPreview() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-white/[0.06] bg-black/20 px-3 py-2">
        <div className="flex gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
        </div>
        <div className="flex flex-1 items-center gap-1.5 rounded-md border border-white/[0.06] bg-white/[0.03] px-2.5 py-1 text-[11px] text-zinc-400">
          <Lock size={10} className="text-accent-to" />
          <span className="truncate font-mono">3000-sbx-i9x2k1.e2b.dev/settings</span>
        </div>
        <RefreshCw size={13} className="text-zinc-500" />
      </div>

      {/* mocked live preview of the app running inside the sandbox */}
      <div className="flex-1 overflow-hidden bg-zinc-100 text-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-2.5">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold">
            <span className="h-4 w-4 rounded bg-gradient-to-br from-accent-via to-accent-to" />
            Orbit
          </div>
          <div className="flex items-center gap-3 text-[11px] text-zinc-500">
            <span>Dashboard</span>
            <span className="text-zinc-900">Settings</span>
            <span>Billing</span>
          </div>
        </div>

        <div className="p-4">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-400">Appearance</p>
          <div className="mt-2 flex items-center justify-between rounded-lg border border-zinc-200 bg-white p-3">
            <div>
              <p className="text-[12px] font-medium text-zinc-800">Dark mode</p>
              <p className="text-[10.5px] text-zinc-500">Match system, or force on / off</p>
            </div>
            <span className="flex h-5 w-9 items-center rounded-full bg-gradient-to-r from-accent-via to-accent-to p-0.5">
              <span className="h-4 w-4 rounded-full bg-white shadow" />
            </span>
          </div>
          <div className="mt-3 h-16 animate-pulse rounded-lg bg-zinc-200/70" />
          <div className="mt-2 h-16 w-2/3 animate-pulse rounded-lg bg-zinc-200/50" />
        </div>
      </div>
    </div>
  );
}

const FILES = [
  { name: "src", dir: true, depth: 0 },
  { name: "theme", dir: true, depth: 1 },
  { name: "ThemeProvider.tsx", dir: false, depth: 2, active: true },
  { name: "components", dir: true, depth: 1 },
  { name: "SettingsPage.tsx", dir: false, depth: 2 },
  { name: "App.tsx", dir: false, depth: 1 },
  { name: "package.json", dir: false, depth: 0, json: true },
];

const CODE_LINES: { n: number; tokens: { t: string; c?: string }[] }[] = [
  { n: 1, tokens: [{ t: "import", c: "text-accent-to" }, { t: " { createContext, useContext, useEffect, useState } " }, { t: "from", c: "text-accent-to" }, { t: ' "react";', c: "text-amber-300/80" }] },
  { n: 2, tokens: [] },
  { n: 3, tokens: [{ t: "type", c: "text-accent-to" }, { t: " Theme = " }, { t: '"light"', c: "text-amber-300/80" }, { t: " | " }, { t: '"dark"', c: "text-amber-300/80" }, { t: ";" }] },
  { n: 4, tokens: [] },
  { n: 5, tokens: [{ t: "const", c: "text-accent-to" }, { t: " ThemeContext = " }, { t: "createContext", c: "text-sky-300/90" }, { t: "<Theme | " }, { t: "null", c: "text-accent-to" }, { t: ">(" }, { t: "null", c: "text-accent-to" }, { t: ");" }] },
  { n: 6, tokens: [] },
  { n: 7, tokens: [{ t: "export function", c: "text-accent-to" }, { t: " ThemeProvider({ children }: { children: React.ReactNode }) {" }] },
  { n: 8, tokens: [{ t: "  const", c: "text-accent-to" }, { t: " [theme, setTheme] = " }, { t: "useState", c: "text-sky-300/90" }, { t: "<Theme>(() =>" }] },
  { n: 9, tokens: [{ t: "    (" }, { t: "localStorage", c: "text-sky-300/90" }, { t: ".getItem(" }, { t: '"theme"', c: "text-amber-300/80" }, { t: ") " }, { t: "as", c: "text-accent-to" }, { t: " Theme) ?? " }, { t: '"dark"', c: "text-amber-300/80" }] },
  { n: 10, tokens: [{ t: "  );" }] },
  { n: 11, tokens: [] },
  { n: 12, tokens: [{ t: "  useEffect", c: "text-sky-300/90" }, { t: "(() => {" }] },
  { n: 13, tokens: [{ t: "    document", c: "text-sky-300/90" }, { t: ".documentElement.dataset.theme = theme;" }] },
  { n: 14, tokens: [{ t: "    localStorage", c: "text-sky-300/90" }, { t: ".setItem(" }, { t: '"theme"', c: "text-amber-300/80" }, { t: ", theme);" }] },
  { n: 15, tokens: [{ t: "  }, [theme]);" }] },
];

function VSCodePreview({ session }: { session: Session }) {
  return (
    <div className="flex h-full flex-col text-[12px]">
      <div className="flex min-h-0 flex-1">
        <div className="w-40 shrink-0 overflow-y-auto border-r border-white/[0.06] bg-black/10 py-2">
          <div className="mb-1.5 flex items-center gap-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            <Search size={10} />
            Explorer
          </div>
          {FILES.map((f) => (
            <div
              key={f.name}
              style={{ paddingLeft: `${10 + f.depth * 12}px` }}
              className={`flex cursor-default items-center gap-1.5 py-[3px] pr-2 ${
                f.active ? "bg-white/[0.06] text-zinc-100" : "text-zinc-400"
              }`}
            >
              {f.dir ? (
                <Folder size={12} className="shrink-0 text-zinc-500" />
              ) : f.json ? (
                <FileJson size={12} className="shrink-0 text-amber-400/80" />
              ) : (
                <File size={12} className="shrink-0 text-accent-to" />
              )}
              <span className="truncate text-[11.5px]">{f.name}</span>
            </div>
          ))}
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center border-b border-white/[0.06] bg-black/10">
            <div className="flex items-center gap-1.5 border-r border-white/[0.06] bg-black/20 px-3 py-1.5 text-[11.5px] text-zinc-200">
              <File size={11} className="text-accent-to" />
              ThemeProvider.tsx
            </div>
          </div>
          <div className="flex-1 overflow-auto bg-black/20 py-2 font-mono leading-relaxed">
            {CODE_LINES.map((line) => (
              <div key={line.n} className="flex px-2 hover:bg-white/[0.02]">
                <span className="w-6 shrink-0 select-none text-right text-zinc-600">{line.n}</span>
                <span className="ml-3 whitespace-pre text-zinc-300">
                  {line.tokens.length === 0
                    ? " "
                    : line.tokens.map((tok, i) => (
                        <span key={i} className={tok.c}>
                          {tok.t}
                        </span>
                      ))}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3 border-t border-white/[0.06] bg-accent-via/90 px-3 py-1 text-[10.5px] text-white/90">
        <span className="flex items-center gap-1">
          <GitBranch size={10} />
          {session.branchName}
        </span>
        <span className="ml-auto flex items-center gap-1">
          <Settings2 size={10} />
          sandbox: {session.repo.name}
        </span>
      </div>
    </div>
  );
}
