"use client";

import { useLayoutEffect, useRef, useState } from "react";

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  const refs = useRef(new Map<T, HTMLButtonElement>());
  const [bar, setBar] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const el = refs.current.get(active);
    if (el) setBar({ left: el.offsetLeft, width: el.offsetWidth });
  }, [active, tabs]);

  return (
    <div role="tablist" className="relative flex gap-0.5 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          ref={(el) => { if (el) refs.current.set(t.id, el); }}
          role="tab"
          type="button"
          aria-selected={t.id === active}
          onClick={() => onChange(t.id)}
          className={`focus-ring whitespace-nowrap px-3 py-2.5 text-[13.5px] font-medium transition-colors duration-150 ${
            t.id === active ? "text-fg" : "text-fg-muted hover:text-fg"
          }`}
        >
          {t.label}
        </button>
      ))}
      <span
        aria-hidden
        className="absolute -bottom-px left-0 h-0.5 rounded bg-accent"
        style={{
          width: bar.width,
          transform: `translateX(${bar.left}px)`,
          transition: "transform 280ms var(--ease-out), width 280ms var(--ease-out)",
        }}
      />
    </div>
  );
}
