export type ThemePreference = "light" | "dark" | "system";

const KEY = "b11-theme";

export function readPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function writePreference(p: ThemePreference): void {
  try {
    if (p === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
  } catch {
    // storage blocked (private mode): the choice lasts for this page only
  }
}

export function resolveTheme(p: ThemePreference, prefersDark: boolean): "light" | "dark" {
  return p === "system" ? (prefersDark ? "dark" : "light") : p;
}

export function systemPrefersDark(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(p: ThemePreference): void {
  document.documentElement.dataset.theme = resolveTheme(p, systemPrefersDark());
}

/** Runs before first paint (inline in <head>) so there is no light flash. */
export const NO_FLASH_SCRIPT = `(function(){try{var p=localStorage.getItem("${KEY}");var d=p==="dark"||(p!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.dataset.theme=d?"dark":"light";}catch(e){}})();`;
