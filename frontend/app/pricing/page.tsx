"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { PlanCards } from "@/components/account/PlanCards";
import { useAccount } from "@/components/account/AccountProvider";
import { account, type PlanCatalog } from "@/lib/account";

export default function PricingPage() {
  const { me } = useAccount();
  const [catalog, setCatalog] = useState<PlanCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    account.plans().then(setCatalog).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6 sm:py-14">
        <div className="rise mx-auto mb-10 max-w-2xl text-center">
          <h1 style={{ "--i": 0 } as React.CSSProperties} className="text-[32px] font-semibold leading-tight tracking-[-0.03em]">
            Plans for every test cycle
          </h1>
          <p style={{ "--i": 1 } as React.CSSProperties} className="mt-3 text-[15px] leading-relaxed text-fg-muted">
            Start free. When you need larger collections or want your results kept, pick a plan. Every plan
            includes unlimited analyses, email receipts and a reminder before it ends.
          </p>
        </div>

        {error && <p role="alert" className="mx-auto max-w-md rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}

        {catalog && (
          <div className="space-y-8">
            <section aria-label="Free plan" className="card flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Free</h2>
                <p className="text-sm text-fg-muted">
                  {catalog.free.uses} analyses with files up to {catalog.free.max_file_mb} MB. No account needed.
                  Results are not saved after your session.
                </p>
              </div>
              {me && !me.plan && (
                <span className="badge badge-low self-start sm:self-auto">
                  {me.limits.uses_remaining} of {me.limits.uses_limit} left
                </span>
              )}
            </section>

            <PlanCards catalog={catalog} currentPlan={me?.plan?.plan ?? null} />

            <section className="grid gap-4 text-sm text-fg-muted md:grid-cols-3">
              <div>
                <h3 className="mb-1 font-medium text-fg">Saved history</h3>
                Each analysis you run on a plan is saved with its collection, generated JMX and manifest, ready to
                download from your dashboard for as long as your plan keeps history.
              </div>
              <div>
                <h3 className="mb-1 font-medium text-fg">Buying again</h3>
                If you buy while a plan is running, the new plan starts the day the current one ends. You never
                lose paid days.
              </div>
              <div>
                <h3 className="mb-1 font-medium text-fg">Emails</h3>
                You get a receipt when you buy, a reminder a few days before your plan ends, and a note when it has
                ended.
              </div>
            </section>

            {!me?.user && (
              <p className="text-center text-sm text-fg-muted">
                Buying a plan needs an account.{" "}
                <Link href="/register" className="font-medium text-accent-soft-ink underline">Create one</Link> or{" "}
                <Link href="/login" className="font-medium text-accent-soft-ink underline">sign in</Link>.
              </p>
            )}
          </div>
        )}
      </main>
    </>
  );
}
