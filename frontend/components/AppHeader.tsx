import { ThemeToggle } from "./ThemeToggle";

export function AppHeader({ right }: { right?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="h-[22px] w-[22px] rounded-[7px] bg-accent" />
          <span className="font-semibold tracking-tight">Baseline11</span>
          <span className="hidden text-sm text-fg-subtle sm:inline">Auto-Correlate</span>
        </div>
        <div className="flex-1" />
        <ThemeToggle />
        {right}
      </div>
    </header>
  );
}
