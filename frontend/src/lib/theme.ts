export const THEMES = ["light", "dark"] as const;
export type ThemeId = (typeof THEMES)[number];
export const THEME_LABELS: Record<ThemeId, string> = { light: "浅色", dark: "深色" };

const STORAGE_KEY = "ych.color-mode";
const LEGACY_KEY = "ych.theme";

export function isThemeId(value: string | null): value is ThemeId {
  return value === "light" || value === "dark";
}

export function getStoredTheme(): ThemeId {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (isThemeId(stored)) return stored;
  window.localStorage.removeItem(LEGACY_KEY);
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: ThemeId): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function setStoredTheme(theme: ThemeId): void {
  window.localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}
