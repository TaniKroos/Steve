// Live workspace view (claude/live-workspace-view-plan.md, editor
// surface rebuilt per claude/live-workspace-v2.md). Two tabs, switched
// manually only -- no auto-switching (plan §6): both keep updating live
// off the same SSE connection underneath regardless of which one is on
// screen (`useSessionChat.ts`), but the user decides when to look at
// which. Files/content/diffs are pull-only, fetched via
// `useSandboxWorkspace.ts` -- never pushed over SSE (plan §2/§3).
import { useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  ChevronsDownUp,
  Code2,
  File,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  Image as ImageIcon,
  Loader2,
  Lock,
  Palette,
  RefreshCw,
  Settings2,
  Terminal as TerminalIcon,
  X,
} from "lucide-react";
import { DiffEditor, Editor } from "@monaco-editor/react";
import { useResizable } from "../lib/useResizable";
import { useSandboxWorkspace } from "../lib/useSandboxWorkspace";
import type { FileEditEntry, FileRemovedEntry, Session, TerminalLine } from "../types";
import { DiffLines } from "./DiffLines";

type Tab = "files" | "terminal";

const PANEL_MIN = 380;
const PANEL_MAX = 880;
const PANEL_DEFAULT = 460;
const EXPLORER_MIN = 140;
const EXPLORER_MAX = 480;
const EXPLORER_DEFAULT = 200;

// Read-only everywhere -- this is a viewer, not a co-editor (v2 plan
// §2: CloudAgent is a supervised IDE, the agent edits and the user
// reviews/redirects through chat, never types directly into a file).
const EDITOR_OPTIONS = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: true },
  fontSize: 12,
  scrollBeyondLastLine: false,
  // Re-measures on container resize automatically -- load-bearing given
  // the panel and the Explorer column are both drag-resizable.
  automaticLayout: true,
};

export function SandboxPanel({
  session,
  onClose,
  lastFileEdit,
  lastFileRemoved,
  fileEditLog,
  terminalLines,
}: {
  session: Session;
  onClose: () => void;
  lastFileEdit: FileEditEntry | null;
  lastFileRemoved: FileRemovedEntry | null;
  fileEditLog: FileEditEntry[];
  terminalLines: TerminalLine[];
}) {
  const [tab, setTab] = useState<Tab>("files");
  const live = session.status === "running" || session.status === "blocked";

  const {
    files,
    filesUnavailable,
    refresh,
    tabs,
    activePath,
    activeContent,
    activeOriginal,
    activeLoading,
    deletedNotice,
    openFile,
    closeTab,
    reviewDiff,
  } = useSandboxWorkspace(session.id, session.status, lastFileEdit, lastFileRemoved);

  // The panel is docked to the right edge of the screen -- its own drag
  // handle lives on its *left* edge, so growing it means dragging left
  // (`invert: true`, see useResizable.ts's comment on why that flip
  // lives in the hook, not duplicated at every call site).
  const { size: panelWidth, isDragging: resizingPanel, onMouseDown: onPanelResizeStart } = useResizable(
    PANEL_DEFAULT,
    { min: PANEL_MIN, max: PANEL_MAX, storageKey: "cloudagent:sandbox-panel-width", invert: true },
  );

  // Manual tabs only (plan §6) -- but a small unread-style dot on the
  // tab the user isn't currently looking at restores some of the
  // "something happened" signal auto-switching used to provide, without
  // reintroducing auto-switching itself.
  const seenEditCount = useRef(0);
  const seenTerminalCount = useRef(0);
  useEffect(() => {
    if (tab === "files") seenEditCount.current = fileEditLog.length;
    if (tab === "terminal") seenTerminalCount.current = terminalLines.length;
  }, [tab, fileEditLog.length, terminalLines.length]);
  const filesHaveActivity = tab !== "files" && fileEditLog.length > seenEditCount.current;
  const terminalHasActivity = tab !== "terminal" && terminalLines.length > seenTerminalCount.current;

  const dirtyPaths = new Set(fileEditLog.map((e) => e.path));

  return (
    <aside
      style={{ width: panelWidth }}
      className={`relative flex h-screen shrink-0 flex-col border-l border-white/[0.06] bg-surface ${resizingPanel ? "" : "transition-[width] duration-75"}`}
    >
      <ResizeHandle onMouseDown={onPanelResizeStart} active={resizingPanel} side="left" />

      <div className="flex items-center gap-1 border-b border-white/[0.06] px-3 py-2.5">
        <TabButton
          icon={Code2}
          label="Files"
          active={tab === "files"}
          showDot={filesHaveActivity}
          onClick={() => setTab("files")}
        />
        <TabButton
          icon={TerminalIcon}
          label="Terminal"
          active={tab === "terminal"}
          showDot={terminalHasActivity}
          onClick={() => setTab("terminal")}
        />

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

      <div className="min-h-0 flex-1">
        {tab === "files" ? (
          <FilesTab
            session={session}
            files={files}
            filesUnavailable={filesUnavailable}
            dirtyPaths={dirtyPaths}
            tabs={tabs}
            activePath={activePath}
            activeContent={activeContent}
            activeOriginal={activeOriginal}
            activeLoading={activeLoading}
            deletedNotice={deletedNotice}
            openFile={openFile}
            closeTab={closeTab}
            reviewDiff={session.status === "blocked" ? reviewDiff : null}
            onRefresh={refresh}
          />
        ) : (
          <TerminalTab lines={terminalLines} />
        )}
      </div>
    </aside>
  );
}

function TabButton({
  icon: Icon,
  label,
  active,
  showDot,
  onClick,
}: {
  icon: typeof Code2;
  label: string;
  active: boolean;
  showDot: boolean;
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
      {showDot && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-to" />}
    </button>
  );
}

// A thin drag-to-resize rail: a wide invisible hit target with a 1px
// line centered inside it (so the line stays subtle but the click/drag
// target is comfortable), highlighting on hover and while actively
// dragging -- standard editor-pane affordance.
function ResizeHandle({
  onMouseDown,
  active,
  side,
}: {
  onMouseDown: (e: React.MouseEvent) => void;
  active: boolean;
  side: "left" | "right";
}) {
  return (
    <div
      onMouseDown={onMouseDown}
      className={`group absolute top-0 bottom-0 z-10 w-2.5 cursor-col-resize ${side === "left" ? "-left-1" : "-right-1"}`}
    >
      <div
        className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${
          active ? "bg-accent-to" : "bg-transparent group-hover:bg-accent-to/50"
        }`}
      />
    </div>
  );
}

// ---------------------------------------------------------------------
// Files tab: real file tree (git ls-files, via useSandboxWorkspace),
// a multi-tab Monaco viewer (read-only) for open files -- rendering a
// real side-by-side DiffEditor in place of the plain view whenever the
// active tab has uncommitted changes -- plus a pre-PR full-diff review
// banner when the session is blocked awaiting confirmation (plan
// §4/§7; editor surface per claude/live-workspace-v2.md §4.1).
// ---------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string;
  dir: boolean;
  children?: TreeNode[];
}

function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode[] = [];
  for (const path of paths) {
    const parts = path.split("/").filter(Boolean);
    let level = root;
    let accumulated = "";
    parts.forEach((part, i) => {
      accumulated = accumulated ? `${accumulated}/${part}` : part;
      const isLast = i === parts.length - 1;
      let node = level.find((n) => n.name === part && n.dir === !isLast);
      if (!node) {
        node = { name: part, path: accumulated, dir: !isLast, children: isLast ? undefined : [] };
        level.push(node);
      }
      if (!isLast) level = node.children!;
    });
  }
  const sortRec = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : a.dir ? -1 : 1));
    for (const n of nodes) if (n.children) sortRec(n.children);
  };
  sortRec(root);
  return root;
}

// Every directory path in the tree, depth-first -- what "collapse all"
// needs to seed its collapsed-set with, since collapsed state is a flat
// set of paths (§ FileTree below), not something the tree structure
// tracks itself.
function allDirPaths(nodes: TreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.dir) {
      paths.push(node.path);
      if (node.children) paths.push(...allDirPaths(node.children));
    }
  }
  return paths;
}

// Monaco's built-in language ids -- a real, different vocabulary from
// Prism's (the syntax highlighter this replaced), e.g. "shell" not
// "bash", "html" not "markup". No dedicated "toml" language ships with
// Monaco; "ini" is a reasonable stand-in (both are line-oriented
// key=value/[section] formats).
const EXTENSION_LANGUAGE: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  go: "go",
  rs: "rust",
  rb: "ruby",
  java: "java",
  json: "json",
  yml: "yaml",
  yaml: "yaml",
  md: "markdown",
  mdx: "markdown",
  css: "css",
  scss: "scss",
  less: "less",
  html: "html",
  htm: "html",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  sql: "sql",
  toml: "ini",
  ini: "ini",
};

function languageForPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_LANGUAGE[ext] ?? "plaintext";
}

// File-type icon + color, keyed off extension (with a couple of
// filename-exact overrides for lockfiles, which carry no useful
// extension of their own) -- the "actual editor feel" this was asked
// for leans heavily on an IDE's file tree reading as color-coded at a
// glance rather than every file looking the same.
const LOCKFILE_NAMES = new Set(["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "Cargo.lock", "poetry.lock"]);

function fileIconFor(name: string): { Icon: typeof File; className: string } {
  if (LOCKFILE_NAMES.has(name) || name.endsWith(".lock")) {
    return { Icon: Lock, className: "text-zinc-500" };
  }
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "ts":
    case "tsx":
    case "js":
    case "jsx":
    case "mjs":
    case "cjs":
      return { Icon: FileCode2, className: "text-sky-400/90" };
    case "json":
      return { Icon: FileJson, className: "text-amber-400/80" };
    case "py":
    case "go":
    case "rs":
    case "rb":
    case "java":
    case "c":
    case "cpp":
      return { Icon: FileCode2, className: "text-emerald-400/80" };
    case "css":
    case "scss":
    case "less":
      return { Icon: Palette, className: "text-pink-400/80" };
    case "html":
    case "htm":
      return { Icon: Code2, className: "text-orange-400/80" };
    case "md":
    case "mdx":
      return { Icon: FileText, className: "text-zinc-400" };
    case "yml":
    case "yaml":
    case "toml":
    case "ini":
    case "env":
      return { Icon: Settings2, className: "text-violet-400/80" };
    case "sh":
    case "bash":
    case "zsh":
      return { Icon: TerminalIcon, className: "text-zinc-400" };
    case "png":
    case "jpg":
    case "jpeg":
    case "gif":
    case "svg":
    case "webp":
    case "ico":
      return { Icon: ImageIcon, className: "text-teal-400/80" };
    default:
      return { Icon: File, className: "text-accent-to" };
  }
}

function FilesTab({
  session,
  files,
  filesUnavailable,
  dirtyPaths,
  tabs,
  activePath,
  activeContent,
  activeOriginal,
  activeLoading,
  deletedNotice,
  openFile,
  closeTab,
  reviewDiff,
  onRefresh,
}: {
  session: Session;
  files: string[];
  filesUnavailable: boolean;
  dirtyPaths: Set<string>;
  tabs: string[];
  activePath: string | null;
  activeContent: string | null;
  activeOriginal: string | null;
  activeLoading: boolean;
  deletedNotice: string | null;
  openFile: (path: string) => void;
  closeTab: (path: string) => void;
  reviewDiff: string | null;
  onRefresh: () => void;
}) {
  const tree = buildTree(files);

  // Lifted above FileTree (rather than each recursive level owning its
  // own local state) so "collapse all" has one flat set of every
  // directory path to seed, instead of needing to reach into N separate
  // component instances -- see allDirPaths above.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (path: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  const collapseAll = () => setCollapsed(new Set(allDirPaths(tree)));

  const {
    size: explorerWidth,
    isDragging: resizingExplorer,
    onMouseDown: onExplorerResizeStart,
  } = useResizable(EXPLORER_DEFAULT, {
    min: EXPLORER_MIN,
    max: EXPLORER_MAX,
    storageKey: "cloudagent:explorer-width",
  });

  // A real diff (not just "has uncommitted changes" in the abstract) --
  // both halves have to have actually loaded, and differ, for the
  // DiffEditor to make sense over the plain viewer.
  const showDiff = activeOriginal !== null && activeContent !== null && activeOriginal !== activeContent;

  return (
    <div className="flex h-full flex-col text-[12px]">
      {reviewDiff && (
        <div className="shrink-0 border-b border-amber-500/20 bg-amber-500/[0.06]">
          <div className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-amber-300/90">
            Ready for your review -- full diff vs {session.branchName}
          </div>
          <pre className="max-h-40 overflow-auto pb-2 font-mono text-[11.5px] leading-relaxed">
            <DiffLines text={reviewDiff || "(no changes yet)"} />
          </pre>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div
          style={{ width: explorerWidth }}
          className="relative flex shrink-0 flex-col overflow-y-auto bg-black/10 py-1.5"
        >
          <div className="mb-1 flex shrink-0 items-center gap-1.5 px-2.5 pt-0.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Explorer
            <span className="text-zinc-700">{files.length > 0 ? files.length : ""}</span>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={onRefresh}
                title="Refresh file tree"
                className="text-zinc-600 transition hover:text-zinc-300"
              >
                <RefreshCw size={11} />
              </button>
              <button onClick={collapseAll} title="Collapse all" className="text-zinc-600 transition hover:text-zinc-300">
                <ChevronsDownUp size={11} />
              </button>
            </div>
          </div>
          {filesUnavailable ? (
            <p className="px-2.5 text-[11px] text-zinc-600">not available right now</p>
          ) : tree.length === 0 ? (
            <p className="px-2.5 text-[11px] text-zinc-600">no files yet</p>
          ) : (
            <FileTree
              nodes={tree}
              activePath={activePath}
              dirtyPaths={dirtyPaths}
              onSelect={openFile}
              collapsed={collapsed}
              onToggle={toggle}
            />
          )}
          <ResizeHandle onMouseDown={onExplorerResizeStart} active={resizingExplorer} side="right" />
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          {tabs.length > 0 && (
            <div className="flex shrink-0 items-center overflow-x-auto border-b border-white/[0.06] bg-black/10">
              {tabs.map((path) => {
                const { Icon: TabIcon, className: tabIconClass } = fileIconFor(path.split("/").pop() ?? path);
                const isActive = path === activePath;
                return (
                  <div
                    key={path}
                    onClick={() => openFile(path)}
                    className={`group flex shrink-0 cursor-pointer items-center gap-1.5 border-r border-b-2 border-white/[0.06] px-3 py-1.5 text-[11.5px] ${
                      isActive
                        ? "border-b-accent-to bg-black/20 text-zinc-200"
                        : "border-b-transparent text-zinc-500 hover:bg-white/[0.02] hover:text-zinc-300"
                    }`}
                  >
                    <TabIcon size={11} className={tabIconClass} />
                    <span className="max-w-[140px] truncate">{path.split("/").pop()}</span>
                    {dirtyPaths.has(path) && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-to" />}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        closeTab(path);
                      }}
                      className="ml-0.5 shrink-0 rounded p-0.5 text-zinc-600 opacity-0 transition hover:bg-white/10 hover:text-zinc-200 group-hover:opacity-100"
                    >
                      <X size={10} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          <div className="relative min-h-0 flex-1 bg-black/20">
            {!activePath ? (
              <p className="p-4 text-[12px] text-zinc-600">
                {deletedNotice ? (
                  <>
                    <span className="text-zinc-400">{deletedNotice}</span> was deleted.
                  </>
                ) : (
                  "Select a file to view it."
                )}
              </p>
            ) : activeLoading ? (
              <div className="flex items-center gap-2 p-4 text-[12px] text-zinc-500">
                <Loader2 size={13} className="animate-spin" />
                loading...
              </div>
            ) : activeContent === null ? (
              <p className="p-4 text-[12px] text-zinc-600">Couldn't load this file.</p>
            ) : showDiff ? (
              <DiffEditor
                key={activePath}
                original={activeOriginal ?? ""}
                modified={activeContent}
                language={languageForPath(activePath)}
                theme="vs-dark"
                options={{ ...EDITOR_OPTIONS, renderSideBySide: true }}
              />
            ) : (
              <Editor
                path={activePath}
                value={activeContent}
                language={languageForPath(activePath)}
                theme="vs-dark"
                options={EDITOR_OPTIONS}
              />
            )}
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

const INDENT_BASE = 8; // px, left padding before depth-0's own icon
const INDENT_STEP = 15; // px, per nesting level
const GUIDE_OFFSET = INDENT_BASE + 7; // px, centers each guide line under its ancestor's icon column

function FileTree({
  nodes,
  activePath,
  dirtyPaths,
  onSelect,
  collapsed,
  onToggle,
  depth = 0,
}: {
  nodes: TreeNode[];
  activePath: string | null;
  dirtyPaths: Set<string>;
  onSelect: (path: string) => void;
  collapsed: Set<string>;
  onToggle: (path: string) => void;
  depth?: number;
}) {
  return (
    <>
      {nodes.map((node) => {
        const isOpen = node.dir && !collapsed.has(node.path);
        const isActive = node.path === activePath;
        const { Icon: FileIcon, className: fileIconClass } = node.dir ? { Icon: Folder, className: "" } : fileIconFor(node.name);

        return (
          <div key={node.path}>
            <div
              style={{ paddingLeft: `${INDENT_BASE + depth * INDENT_STEP}px` }}
              className={`group relative flex cursor-pointer items-center gap-1.5 py-[3px] pr-2 ${
                isActive ? "bg-white/[0.07] text-zinc-100" : "text-zinc-400 hover:bg-white/[0.03]"
              }`}
              onClick={() => (node.dir ? onToggle(node.path) : onSelect(node.path))}
            >
              {isActive && <span className="absolute inset-y-0 left-0 w-[2px] bg-accent-to" />}

              {/* Indent guides -- one faint vertical line per ancestor
                  depth level, each row drawing only its own segment so
                  consecutive rows read as one continuous rule (no need
                  for a single tall absolutely-positioned line spanning
                  a whole subtree). */}
              {Array.from({ length: depth }).map((_, i) => (
                <span
                  key={i}
                  className="absolute top-0 bottom-0 w-px bg-white/[0.05]"
                  style={{ left: `${GUIDE_OFFSET + i * INDENT_STEP}px` }}
                />
              ))}

              {node.dir ? (
                <ChevronRight
                  size={10}
                  strokeWidth={2.5}
                  className={`shrink-0 text-zinc-600 transition-transform duration-100 ${isOpen ? "rotate-90" : ""}`}
                />
              ) : (
                <span className="w-2.5 shrink-0" />
              )}
              {node.dir ? (
                isOpen ? (
                  <FolderOpen size={12} className="shrink-0 text-accent-to/70" />
                ) : (
                  <Folder size={12} className="shrink-0 text-zinc-500" />
                )
              ) : (
                <FileIcon size={12} className={`shrink-0 ${fileIconClass}`} />
              )}
              <span className="truncate text-[11.5px]">{node.name}</span>
              {dirtyPaths.has(node.path) && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent-to" />}
            </div>
            {node.dir && node.children && isOpen && (
              <FileTree
                nodes={node.children}
                activePath={activePath}
                dirtyPaths={dirtyPaths}
                onSelect={onSelect}
                collapsed={collapsed}
                onToggle={onToggle}
                depth={depth + 1}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------
// Terminal tab: live shell_output, needs no backend change -- the data
// already flows (plan §8 step 6). Plain rendering, no ANSI parsing yet
// (deferred per the plan until real output volume/noise is visible).
// ---------------------------------------------------------------------

function TerminalTab({ lines }: { lines: TerminalLine[] }) {
  // Scrolls this container's own scrollTop directly -- deliberately not
  // `element.scrollIntoView()`, which walks up *every* scrollable
  // ancestor (including AppShell's root flex row, an overflow-hidden
  // container that's a legitimate scroll target even with no visible
  // scrollbar) and was actually scrolling that ancestor sideways to
  // bring this element into view, permanently shifting the whole page
  // layout the first time the Terminal tab mounted. Scoping the scroll
  // to exactly this element removes any chance of touching an ancestor.
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div ref={containerRef} className="h-full overflow-auto bg-black/30 p-3 font-mono text-[12px] leading-relaxed">
      {lines.length === 0 ? (
        <p className="text-zinc-600">No shell output yet.</p>
      ) : (
        lines.map((line, i) => (
          <span key={i} className={line.stream === "stderr" ? "text-rose-300/90" : "text-zinc-300"}>
            {line.chunk}
          </span>
        ))
      )}
    </div>
  );
}
