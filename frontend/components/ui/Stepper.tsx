import { CheckIcon } from "@phosphor-icons/react";

export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol aria-label="Progress" className="mb-8 flex max-w-xl items-center">
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={label} data-state={state} aria-current={state === "current" ? "step" : undefined} className="flex flex-1 items-center last:flex-none">
            <span className={`flex items-center gap-2 whitespace-nowrap text-[13px] ${state === "current" ? "font-medium text-fg" : "text-fg-subtle"}`}>
              <span
                className={`grid h-6 w-6 place-items-center rounded-full border text-xs font-semibold transition-colors duration-200 ${
                  state === "current"
                    ? "border-accent bg-accent text-accent-ink"
                    : state === "done"
                      ? "border-transparent bg-accent-soft text-accent-soft-ink"
                      : "border-line-strong bg-surface"
                }`}
              >
                {state === "done" ? <CheckIcon size={12} weight="bold" aria-hidden /> : i + 1}
              </span>
              {/* On phones only the current step is labelled; the rest stay readable to screen readers. */}
              <span className={state === "current" ? undefined : "sr-only sm:not-sr-only"}>{label}</span>
            </span>
            {i < steps.length - 1 && (
              <span aria-hidden className="relative mx-2 h-0.5 sm:mx-3 min-w-6 flex-1 overflow-hidden rounded bg-line">
                <span
                  className="absolute inset-0 origin-left bg-accent"
                  style={{
                    transform: `scaleX(${i < current ? 1 : 0})`,
                    transition: "transform 360ms var(--ease-in-out)",
                  }}
                />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
