// Generic drag-to-resize primitive, shared by the sandbox panel's own
// width and the file-explorer/content split inside it (SandboxPanel.tsx)
// -- both are "drag a vertical handle, clamp to [min, max], remember it"
// with nothing else in common, so one small hook instead of two
// near-duplicate ones.
import { useCallback, useEffect, useRef, useState } from "react";

interface UseResizableOptions {
  min: number;
  max: number;
  // Persists across reloads when set -- an editor-feel detail (VS Code
  // remembers panel widths too), not load-bearing for the resize itself.
  storageKey?: string;
  // The sandbox panel is docked to the right edge of the screen, so its
  // drag handle sits on its *left* edge -- dragging left (a negative
  // clientX delta) should grow it, not shrink it. The explorer/content
  // split's handle has no such flip: dragging right grows the explorer
  // directly.
  invert?: boolean;
}

export function useResizable(defaultSize: number, { min, max, storageKey, invert = false }: UseResizableOptions) {
  const [size, setSize] = useState(() => {
    if (!storageKey) return defaultSize;
    const stored = Number(localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored >= min && stored <= max ? stored : defaultSize;
  });

  // Mirrors `size` for the mousemove/mouseup listeners below -- those are
  // registered once (empty-ish dep array) rather than re-subscribed on
  // every pixel of movement, so they'd otherwise close over a stale
  // `size` from whenever the drag started.
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const dragStart = useRef<{ x: number; size: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStart.current = { x: e.clientX, size: sizeRef.current };
    setIsDragging(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragStart.current) return;
      const delta = e.clientX - dragStart.current.x;
      const next = dragStart.current.size + (invert ? -delta : delta);
      setSize(Math.min(max, Math.max(min, next)));
    };
    const onUp = () => {
      if (!dragStart.current) return;
      dragStart.current = null;
      setIsDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (storageKey) localStorage.setItem(storageKey, String(sizeRef.current));
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [min, max, invert, storageKey]);

  return { size, isDragging, onMouseDown };
}
