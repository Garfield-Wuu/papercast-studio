import { useEffect, useState } from "react";

const STORAGE_KEY = "papercast.theme";
type Theme = "light" | "dark";

function readPreferred(): Theme {
  // 1. honour an explicit user choice in localStorage
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  // 2. fall back to the OS preference
  if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/**
 * useTheme — keeps `[data-theme]` on <html> in sync with the user's
 * explicit choice (persisted to localStorage) or, if none, the OS
 * preference. Listens for `prefers-color-scheme` changes when the
 * user hasn't picked manually.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = readPreferred();
    applyTheme(t);
    return t;
  });

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Track OS changes only when the user hasn't pinned a preference.
  useEffect(() => {
    if (localStorage.getItem(STORAGE_KEY)) return;
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const onChange = () => setTheme(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const set = (next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next);
    setTheme(next);
  };
  const toggle = () => set(theme === "dark" ? "light" : "dark");
  const reset = () => {
    localStorage.removeItem(STORAGE_KEY);
    setTheme(readPreferred());
  };

  return { theme, set, toggle, reset };
}
