"use client";

import Link from "next/link";
import { CheckIcon } from "@phosphor-icons/react";
import { formatPrice, perMonth, periodLabel, type Plan, type PlanCatalog, type PlanId } from "@/lib/account";

const HIGHLIGHT: PlanId = "semiannual";

function savings(plan: Plan, monthly: Plan | undefined): string | null {
  if (!monthly || plan.id === monthly.id) return null;
  const full = monthly.price_cents * plan.months;
  const pct = Math.round((1 - plan.price_cents / full) * 100);
  return pct > 0 ? `Save ${pct}%` : null;
}

/** The three paid plans side by side, each linking to checkout. */
export function PlanCards({
  catalog,
  currentPlan,
  compact = false,
}: {
  catalog: PlanCatalog;
  currentPlan?: PlanId | null;
  compact?: boolean;
}) {
  const monthly = catalog.plans.find((p) => p.id === "monthly");
  return (
    <div className={`grid gap-4 ${compact ? "sm:grid-cols-3" : "md:grid-cols-3"}`}>
      {catalog.plans.map((plan) => {
        const featured = plan.id === HIGHLIGHT;
        const save = savings(plan, monthly);
        return (
          <article
            key={plan.id}
            aria-label={`${plan.name} plan`}
            className={`card relative flex flex-col p-5 ${featured ? "border-accent ring-1 ring-accent" : ""}`}
          >
            {featured && (
              <span className="badge badge-accent absolute -top-3 left-5">Most popular</span>
            )}
            <h3 className="text-base font-semibold">{plan.name}</h3>
            <p className="mt-2 flex items-baseline gap-1.5">
              <span className="text-3xl font-semibold tracking-tight">{formatPrice(plan.price_cents, plan.currency)}</span>
              <span className="text-sm text-fg-muted">{periodLabel(plan.months)}</span>
            </p>
            <p className="mt-1 h-5 text-xs text-fg-subtle">
              {plan.months > 1 ? `${perMonth(plan)} a month` : "Billed monthly"}
              {save && <span className="ml-2 font-medium text-ok">{save}</span>}
            </p>
            <ul className="mt-4 flex-1 space-y-2 text-sm">
              {[
                "Unlimited analyses",
                `Files up to ${plan.max_file_mb} MB`,
                `History and downloads kept ${plan.retention_days} days`,
                "Email receipt and renewal reminder",
              ].map((feature) => (
                <li key={feature} className="flex gap-2">
                  <CheckIcon size={16} weight="bold" className="mt-0.5 flex-none text-ok" aria-hidden />
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
            <Link
              href={`/checkout?plan=${plan.id}`}
              className={`${featured ? "btn" : "btn-secondary"} mt-5 w-full`}
              aria-label={`Choose the ${plan.name} plan`}
            >
              {currentPlan === plan.id ? "Extend this plan" : `Choose ${plan.name}`}
            </Link>
          </article>
        );
      })}
    </div>
  );
}
