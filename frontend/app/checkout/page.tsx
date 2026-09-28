"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircleIcon, LockSimpleIcon } from "@phosphor-icons/react";
import { AppHeader } from "@/components/AppHeader";
import { useAccount } from "@/components/account/AccountProvider";
import { FormError } from "@/components/account/AuthCard";
import {
  account,
  formatDate,
  formatPrice,
  periodLabel,
  type PlanCatalog,
  type Subscription,
} from "@/lib/account";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2 text-sm">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  );
}

function Checkout() {
  const planId = useSearchParams().get("plan") ?? "";
  const { me, loading, refresh } = useAccount();
  const [catalog, setCatalog] = useState<PlanCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Subscription | null>(null);

  useEffect(() => {
    account.plans().then(setCatalog).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const plan = catalog?.plans.find((p) => p.id === planId);
  const next = encodeURIComponent(`/checkout?plan=${planId}`);

  async function pay() {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const { subscription } = await account.checkout(plan.id);
      setDone(subscription);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (done && me?.user) {
    return (
      <div className="card rise mx-auto max-w-md p-6 text-center sm:p-8">
        <CheckCircleIcon size={44} weight="fill" className="mx-auto text-ok" aria-hidden />
        <h1 className="mt-3 text-2xl font-semibold">Payment successful</h1>
        <p className="mt-2 text-sm text-fg-muted">
          {done.status === "scheduled"
            ? `Your ${done.plan_name} plan starts on ${formatDate(done.starts_at)}, when your current plan ends.`
            : `Your ${done.plan_name} plan is active until ${formatDate(done.expires_at)}.`}{" "}
          A receipt is on its way to {me.user.email}.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Link href="/" className="btn">Start an analysis</Link>
          <Link href="/dashboard" className="btn-secondary">Open dashboard</Link>
        </div>
      </div>
    );
  }

  if (!catalog || loading) return <p className="text-center text-sm text-fg-muted">Loading...</p>;

  if (!plan) {
    return (
      <div className="card mx-auto max-w-md p-6 text-center">
        <h1 className="text-lg font-semibold">Choose a plan first</h1>
        <Link href="/pricing" className="btn mt-4">See the plans</Link>
      </div>
    );
  }

  if (!me?.user) {
    return (
      <div className="card rise mx-auto max-w-md p-6 text-center sm:p-8">
        <h1 className="text-xl font-semibold">Create an account to continue</h1>
        <p className="mt-2 text-sm text-fg-muted">
          Your {plan.name} plan, receipts and saved analyses are kept with your account.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Link href={`/register?next=${next}`} className="btn">Create account</Link>
          <Link href={`/login?next=${next}`} className="btn-secondary">Sign in</Link>
        </div>
      </div>
    );
  }

  const queued = [me.plan, ...me.upcoming].filter(Boolean).at(-1);
  const startsLater = Boolean(queued);
  return (
    <div className="card rise mx-auto max-w-lg p-6 sm:p-8">
      <h1 className="text-2xl font-semibold tracking-tight">Checkout</h1>
      <p className="mt-1 text-sm text-fg-muted">Signed in as {me.user.email}</p>
      <dl className="mt-6 divide-y divide-line border-y border-line">
        <Row label="Plan" value={plan.name} />
        <Row label="Price" value={`${formatPrice(plan.price_cents, plan.currency)} ${periodLabel(plan.months)}`} />
        <Row label="File size limit" value={`${plan.max_file_mb} MB per file`} />
        <Row label="History kept" value={`${plan.retention_days} days`} />
        <Row label="Starts" value={startsLater && queued ? `${formatDate(queued.expires_at)} (after your current plan)` : "Today"} />
      </dl>
      <div className="mt-4 flex items-center justify-between text-base font-semibold">
        <span>Total</span>
        <span>{formatPrice(plan.price_cents, plan.currency)}</span>
      </div>
      {catalog.billing_provider === "demo" && (
        <p className="mt-4 rounded-[10px] bg-warn-soft px-3 py-2 text-sm text-warn">
          Demo checkout: no card is charged. The plan activates as soon as you confirm.
        </p>
      )}
      <div className="mt-4">
        <FormError message={error} />
      </div>
      <button className="btn mt-4 w-full" disabled={busy || catalog.billing_provider === "disabled"} onClick={pay}>
        <LockSimpleIcon size={16} weight="bold" aria-hidden />
        {busy ? "Processing..." : `Pay ${formatPrice(plan.price_cents, plan.currency)}`}
      </button>
      <p className="mt-3 text-center text-xs text-fg-subtle">
        <Link href="/pricing" className="underline">Change plan</Link>
      </p>
    </div>
  );
}

export default function CheckoutPage() {
  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6 sm:py-16">
        <Suspense>
          <Checkout />
        </Suspense>
      </main>
    </>
  );
}
