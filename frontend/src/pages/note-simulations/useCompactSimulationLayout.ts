import { useEffect, useState } from "react";

const QUERY = "(max-width: 1023px)";

export function useCompactSimulationLayout(): boolean {
  const [compact, setCompact] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    const media = window.matchMedia(QUERY);
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return compact;
}
