"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { XIcon } from "@phosphor-icons/react";
import { PLAN_LIMIT_EVENT, type PlanLimitDetail } from "@/lib/api";
import { account, type PlanCatalog } from "@/lib/account";
import { useAccount } from "./AccountProvider";
import { PlanCards } from "./PlanCards";

/** Opens with the plans whenever the API says the visitor's plan does not
 * allow what they tried: no free analyses left, or a file over the limit. */
export function PlanLimitDialog() {
  const [detail, setDetail] = useState<PlanLimitDetail | null>(null);
  const [catalog, setCatalog] = useState<PlanCatalog | null>(null);
  const { me, refresh } = useAccount();
  const heading = useRef<HTMLHeadingElement>(null);
  const pathname = usePathname();

  // The dialog lives in the layout, which survives navigation: choosing a
  // plan (or any other link) must not leave it covering the next page.
  useEffect(() => {
    setDetail(null);
  }, [pathname]);

  useEffect(() => {
    function onLimit(event: Event) {
      setDetail((event as CustomEvent<PlanLimitDetail>).detail);
      void refresh();
    }
    window.addEventListener(PLAN_LIMIT_EVENT, onLimit);
    return () => window.removeEventListener(PLAN_LIMIT_EVENT, onLimit);
  }, [refresh]);

  useEffect(() => {
    if (!detail) return;
    heading.current?.focus();
    if (!catalog) account.plans().then(setCatalog).catch(() => {});
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDetail(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail, catalog]);

  if (!detail) return null;
  const title = detail.code === "usage_limit_reached" ? "You have used your free analyses" : "This file is too large for your plan";

  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-canvas/80 p-4 backdrop-blur-sm">
      <div role="dialog" aria-modal="true" aria-labelledby="plan-limit-title" className="card w-full max-w-4xl p-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="plan-limit-title" ref={heading} tabIndex={-1} className="text-xl font-semibold outline-none">
              {title}
            </h2>
            <p className="mt-1 text-sm text-fg-muted">{detail.message}</p>
          </div>
          <button type="button" className="btn-ghost p-2" aria-label="Close" onClick={() => setDetail(null)}>
            <XIcon size={18} aria-hidden />
          </button>
        </div>
        {catalog ? (
          <PlanCards catalog={catalog} currentPlan={me?.plan?.plan ?? null} compact />
        ) : (
          <p className="text-sm text-fg-muted">Loading plans...</p>
        )}
        {!me?.user && (
          <p className="mt-4 text-center text-sm text-fg-muted">
            Already have a plan?{" "}
            <Link href="/login" className="font-medium text-accent-soft-ink underline" onClick={() => setDetail(null)}>
              Sign in
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
