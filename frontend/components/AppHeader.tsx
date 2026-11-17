import Link from "next/link";
import { AccountMenu } from "./account/AccountMenu";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function AppHeader({ right }: { right?: React.ReactNode }) {
  return (
    <header className="relative top-0 z-20 border-b border-line bg-surface/95 backdrop-blur-md lg:sticky">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-30 focus:rounded-lg focus:bg-surface focus:p-3">Skip to main content</a>
      <div className="mx-auto flex min-h-16 max-w-6xl flex-wrap items-center gap-2 px-4 py-2 sm:gap-4 sm:px-6 lg:h-16 lg:flex-nowrap lg:py-0">
        <Link href="/" className="flex items-center gap-3" aria-label="Baseline11 Auto-Correlate home">
          <Logo className="h-6 w-auto sm:h-8" />
          <span aria-hidden className="hidden h-5 w-px bg-line sm:block" />
          <span className="hidden text-sm text-fg-subtle sm:inline">Auto-Correlate</span>
        </Link>
        <div className="flex-1" />
        <a href="/guide" target="_blank" rel="noopener noreferrer" className="btn-ghost text-sm">User guide</a>
        <AccountMenu />
        <ThemeToggle />
        {right}
      </div>
    </header>
  );
}
