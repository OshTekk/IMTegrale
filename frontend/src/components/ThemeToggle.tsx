import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { applyTheme, currentTheme, subscribeTheme, type ColorTheme } from "../lib/theme";

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, setTheme] = useState<ColorTheme>(currentTheme);
  useEffect(() => subscribeTheme(setTheme), []);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      className={`icon-button theme-toggle ${className}`.trim()}
      type="button"
      onClick={() => applyTheme(next, true)}
      aria-label={`Utiliser le thème ${next === "dark" ? "sombre" : "clair"}`}
      title={`Thème ${next === "dark" ? "sombre" : "clair"}`}
    >
      {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
