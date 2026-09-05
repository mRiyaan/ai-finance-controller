"use client";

import { useEffect, useState } from "react";

const THEME_STORAGE_KEY = "ai-finance-controller-theme";

function getInitialTheme() {
  if (typeof window === "undefined") {
    return "light";
  }

  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);

  if (storedTheme === "light" || storedTheme === "dark") {
    return storedTheme;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState("light");
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const initialTheme = getInitialTheme();

    setTheme(initialTheme);
    document.documentElement.dataset.theme = initialTheme;
    setIsReady(true);
  }, []);

  function toggleTheme() {
    const nextTheme = theme === "light" ? "dark" : "light";

    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  }

  const nextThemeLabel = theme === "light" ? "dark" : "light";

  return (
    <button
      aria-label={`Switch to ${nextThemeLabel} theme`}
      className="theme-toggle"
      disabled={!isReady}
      onClick={toggleTheme}
      title={`Switch to ${nextThemeLabel} theme`}
      type="button"
    >
      <span aria-hidden="true" className="theme-toggle-icon">
        {theme === "light" ? "☾" : "☀"}
      </span>

      <span className="theme-toggle-label">
        {theme === "light" ? "Dark mode" : "Light mode"}
      </span>
    </button>
  );
}