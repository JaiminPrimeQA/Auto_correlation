"use client";

import { useId, useState } from "react";
import { CaretRightIcon, CheckIcon, LockSimpleIcon } from "@phosphor-icons/react";

const COPY = {
  local: {
    promise: "Local processing uses memory and temporary files. Download the results you need before your session expires.",
    facts: [
      "Analysis state is held in server memory. Collection runs also write inputs, credentials and reports to a temporary workspace.",
      "Each run's temporary workspace is deleted as soon as the run ends.",
      'Results expire after the configured session lifetime (30 minutes by default). "New analysis" requests deletion of the current results.',
      "Recognized credentials are masked and externalized from JMX by default. Enabling 'embed static secrets' includes captured credentials in the downloaded file. Review files before sharing.",
    ],
  },
  aws: {
    promise: "Hosted execution stores job files encrypted. Session expiry and physical file deletion follow different schedules.",
    facts: [
      "Job files are encrypted in storage; API access requires the owning account. Analysis state currently lives in server memory.",
      "Each run happens in a fresh, isolated task that is discarded when it ends.",
      'Results expire after the configured session lifetime (30 minutes by default). Job files are cleaned up after processing; leftover objects have a one-day storage lifecycle rule, with asynchronous deletion.',
      "Recognized credentials are masked and externalized from JMX by default. Enabling 'embed static secrets' includes captured credentials in the downloaded file. Review files before sharing.",
    ],
  },
} as const;

export function PrivacyNote({ mode }: { mode?: "local" | "aws" }) {
  const resolved = mode ?? (process.env.NEXT_PUBLIC_PRIVACY_MODE === "aws" ? "aws" : "local");
  const copy = COPY[resolved];
  const [open, setOpen] = useState(false);
  const panelId = useId();

  return (
    <div className="border-t border-line pt-4">
      <div className="flex items-start gap-2.5 text-[13.5px] leading-snug text-fg">
        <span className="grid h-[22px] w-[22px] flex-none place-items-center rounded-md bg-ok-soft text-ok">
          <LockSimpleIcon size={13} weight="bold" aria-hidden />
        </span>
        <span>{copy.promise}</span>
      </div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="focus-ring ml-8 mt-2 inline-flex items-center gap-1 rounded text-[13px] font-medium text-accent-soft-ink"
      >
        <CaretRightIcon
          size={12}
          weight="bold"
          aria-hidden
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 220ms var(--ease-out)" }}
        />
        How we handle your data
      </button>
      <div
        id={panelId}
        inert={!open}
        aria-hidden={!open}
        className="ml-8 grid"
        style={{ gridTemplateRows: open ? "1fr" : "0fr", transition: "grid-template-rows 260ms var(--ease-out)" }}
      >
        <ul className="overflow-hidden text-[13px] leading-snug text-fg-muted" style={{ visibility: open ? "visible" : "hidden" }}>
          {copy.facts.map((f) => (
            <li key={f} className="flex gap-2 pt-1.5">
              <CheckIcon size={13} weight="bold" className="mt-0.5 flex-none text-ok" aria-hidden />
              <span>{f}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
