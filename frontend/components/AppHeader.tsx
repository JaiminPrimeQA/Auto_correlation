import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function AppHeader({ right }: { right?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/85 backdrop-blur-md">
      <div className="mx-auto flex min-h-16 max-w-6xl flex-wrap items-center gap-2 px-4 py-2 sm:h-16 sm:flex-nowrap sm:gap-4 sm:px-6 sm:py-0">
        <div className="flex items-center gap-3">
          <Logo className="h-6 w-auto sm:h-8" />
          <span aria-hidden className="hidden h-5 w-px bg-line sm:block" />
          <span className="hidden text-sm text-fg-subtle sm:inline">Auto-Correlate</span>
        </div>
        <div className="flex-1" />
        <a href="/guide" target="_blank" rel="noopener noreferrer" className="btn-ghost text-sm">User guide</a>
        <ThemeToggle />
        {right}
      </div>
    </header>
  );
}
