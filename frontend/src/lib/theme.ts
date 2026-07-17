export type ColorTheme = "light" | "dark";

const STORAGE_KEY = "imtegrale:theme";
const CHANGE_EVENT = "imtegrale:theme-change";

function storedTheme(): ColorTheme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme(): ColorTheme {
  return storedTheme() ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

export function applyTheme(theme: ColorTheme, persist = false) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#101918" : "#0b4f50");
  if (persist) {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // The theme still applies when private storage is unavailable.
    }
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: theme }));
}

export function initializeTheme() {
  applyTheme(currentTheme());
}

export function subscribeTheme(listener: (theme: ColorTheme) => void) {
  const onTheme = (event: Event) => listener((event as CustomEvent<ColorTheme>).detail);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY) return;
    const theme = currentTheme();
    applyTheme(theme);
    listener(theme);
  };
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystem = () => {
    if (storedTheme()) return;
    const theme = currentTheme();
    applyTheme(theme);
    listener(theme);
  };
  window.addEventListener(CHANGE_EVENT, onTheme);
  window.addEventListener("storage", onStorage);
  media.addEventListener("change", onSystem);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onTheme);
    window.removeEventListener("storage", onStorage);
    media.removeEventListener("change", onSystem);
  };
}
