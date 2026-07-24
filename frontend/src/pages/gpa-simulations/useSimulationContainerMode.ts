import { useEffect, useState } from "react";

const COMPACT_WORKSPACE_WIDTH = 920;

export function useSimulationContainerMode(container: HTMLElement | null): "compact" | "wide" {
  const [mode, setMode] = useState<"compact" | "wide">("compact");

  useEffect(() => {
    if (!container) return;
    const update = () => {
      const width = container.getBoundingClientRect().width;
      setMode(width >= COMPACT_WORKSPACE_WIDTH ? "wide" : "compact");
    };
    update();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(container);
    return () => observer.disconnect();
  }, [container]);

  return mode;
}
