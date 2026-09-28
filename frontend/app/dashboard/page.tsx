"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DownloadSimpleIcon, TrashIcon } from "@phosphor-icons/react";
import { AppHeader } from "@/components/AppHeader";
import { useAccount } from "@/components/account/AccountProvider";
import {
  account,
  formatBytes,
  formatDate,
  formatPrice,
  type HistoryItem,
  type Me,
  type Subscription,
} from "@/lib/account";

function PlanPanel({ me }: { me: Me }) {
  const plan = me.plan;
  if (!plan) {
    const { uses_remaining, uses_limit, max_file_mb } = me.limits;
    return (
      <section aria-labelledby="plan-heading" className="card p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Current plan</p>
            <h2 id="plan-heading" className="mt-1 text-xl font-semibold">Free</h2>
            <p className="mt-1 text-sm text-fg-muted">
              {uses_remaining} of {uses_limit} free analyses left, files up to {max_file_mb} MB. History is not saved
              on the free plan.
            </p>
          </div>
          <Link href="/pricing" className="btn">Choose a plan</Link>
        </div>
      </section>
    );
  }

  const total = plan.expires_at - plan.starts_at;
  const used = Math.min(1, Math.max(0, (Date.now() / 1000 - plan.starts_at) / total));
  const ending = (plan.days_left ?? 0) <= 3;
  return (
    <section aria-labelledby="plan-heading" className="card p-5 sm:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Current plan</p>
          <h2 id="plan-heading" className="mt-1 flex items-center gap-2 text-xl font-semibold">
            {plan.plan_name} <span className="badge badge-high">Active</span>
          </h2>
          <p className="mt-1 text-sm text-fg-muted">
            Active until {formatDate(plan.expires_at)}
            <span className={ending ? "font-medium text-warn" : ""}>
              {" "}({plan.days_left === 0 ? "ends today" : `${plan.days_left} day${plan.days_left === 1 ? "" : "s"} left`})
            </span>
          </p>
        </div>
        <Link href="/pricing" className={ending ? "btn" : "btn-secondary"}>{ending ? "Renew now" : "Extend or change plan"}</Link>
      </div>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface2" aria-hidden>
        <div className="h-full rounded-full bg-accent" style={{ width: `${used * 100}%` }} />
      </div>
      <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-3">
        <div><dt className="text-fg-subtle">Analyses</dt><dd className="font-medium">Unlimited</dd></div>
        <div><dt className="text-fg-subtle">File size limit</dt><dd className="font-medium">{me.limits.max_file_mb} MB per file</dd></div>
        <div><dt className="text-fg-subtle">History kept</dt><dd className="font-medium">{me.limits.history_retention_days} days</dd></div>
      </dl>
      {me.upcoming.map((u) => (
        <p key={u.id} className="mt-4 rounded-[10px] bg-accent-soft px-3 py-2 text-sm text-accent-soft-ink">
          Next: your {u.plan_name} plan starts on {formatDate(u.starts_at)} and runs until {formatDate(u.expires_at)}.
        </p>
      ))}
    </section>
  );
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

const STATUS_LABEL: Record<string, string> = { generated: "JMX generated", validated: "JMX validated" };

function HistoryPanel({
  items, paid, onDelete,
}: { items: HistoryItem[] | null; paid: boolean; onDelete: (item: HistoryItem) => void }) {
  return (
    <section aria-labelledby="history-heading" className="card p-5 sm:p-6">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="history-heading" className="text-lg font-semibold">Analysis history</h2>
        <p className="text-xs text-fg-subtle">Each entry is removed automatically when its retention period ends.</p>
      </div>
      {items === null ? (
        <p className="text-sm text-fg-muted">Loading...</p>
      ) : items.length === 0 ? (
        <div className="rounded-[10px] border border-dashed border-line-strong p-6 text-center text-sm text-fg-muted">
          {paid ? (
            <>No saved analyses yet. <Link href="/" className="font-medium text-accent-soft-ink underline">Run one</Link> and it appears here with its files.</>
          ) : (
            <>Saved history is part of the paid plans. <Link href="/pricing" className="font-medium text-accent-soft-ink underline">See the plans</Link>.</>
          )}
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {items.map((item) => (
            <li key={item.id} className="flex flex-col gap-3 py-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <p className="truncate font-medium">{item.collection_name}</p>
                <p className="mt-0.5 text-xs text-fg-muted">
                  {formatDate(item.created_at)}
                  {item.request_count != null && ` · ${plural(item.request_count, "request")}`}
                  {item.correlation_count != null && ` · ${plural(item.correlation_count, "correlation")}`}
                  {item.collection_bytes > 0 && ` · ${formatBytes(item.collection_bytes)}`}
                </p>
                <p className="mt-1 flex flex-wrap gap-1.5">
                  <span className={`badge ${item.jmx_status ? "badge-high" : "badge-low"}`}>
                    {item.jmx_status ? STATUS_LABEL[item.jmx_status] ?? item.jmx_status : "No JMX generated yet"}
                  </span>
                  <span className={`badge ${item.days_left <= 1 ? "badge-medium" : "badge-low"}`}>
                    Kept until {formatDate(item.expires_at)}
                  </span>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {(["collection", "jmx", "manifest"] as const).map((kind) =>
                  item.downloads[kind] ? (
                    <a key={kind} href={item.downloads[kind]} download className="btn-secondary px-3 py-2 text-xs">
                      <DownloadSimpleIcon size={14} aria-hidden />
                      {kind === "collection" ? "Collection" : kind === "jmx" ? "JMX" : "Manifest"}
                    </a>
                  ) : null,
                )}
                <button type="button" className="btn-ghost px-3 py-2 text-xs text-danger" onClick={() => onDelete(item)}
                  aria-label={`Delete ${item.collection_name}`}>
                  <TrashIcon size={14} aria-hidden />
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PurchasesPanel({ items }: { items: Subscription[] | null }) {
  if (!items || items.length === 0) return null;
  return (
    <section aria-labelledby="purchases-heading" className="card p-5 sm:p-6">
      <h2 id="purchases-heading" className="mb-4 text-lg font-semibold">Purchases</h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className="text-xs text-fg-subtle">
            <tr>
              <th className="pb-2 font-medium">Plan</th>
              <th className="pb-2 font-medium">Status</th>
              <th className="pb-2 font-medium">Period</th>
              <th className="pb-2 font-medium">Amount</th>
              <th className="pb-2 font-medium">Reference</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {items.map((s) => (
              <tr key={s.id}>
                <td className="py-2.5 font-medium">{s.plan_name}</td>
                <td className="py-2.5">
                  <span className={`badge ${s.status === "active" ? "badge-high" : s.status === "scheduled" ? "badge-accent" : "badge-low"}`}>
                    {s.status === "active" ? "Active" : s.status === "scheduled" ? "Starts later" : "Ended"}
                  </span>
                </td>
                <td className="py-2.5 text-fg-muted">{formatDate(s.starts_at)} to {formatDate(s.expires_at)}</td>
                <td className="py-2.5">{formatPrice(s.price_cents, s.currency)}</td>
                <td className="mono py-2.5 text-xs text-fg-muted">{s.payment_ref}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const { me, loading } = useAccount();
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [purchases, setPurchases] = useState<Subscription[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, p] = await Promise.all([account.history(), account.subscriptions()]);
      setHistory(h.items);
      setPurchases(p.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (loading) return;
    if (!me?.user) router.replace("/login?next=/dashboard");
    else void load();
  }, [loading, me?.user, router, load]);

  async function remove(item: HistoryItem) {
    if (!window.confirm(`Delete "${item.collection_name}" and its saved files? This cannot be undone.`)) return;
    try {
      await account.deleteHistory(item.id);
      setHistory((current) => current?.filter((h) => h.id !== item.id) ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <AppHeader right={<Link href="/" className="btn-ghost whitespace-nowrap">New analysis</Link>} />
      <main className="mx-auto max-w-6xl space-y-5 px-4 py-8 sm:px-6">
        {!me?.user ? (
          <p className="text-sm text-fg-muted">Loading...</p>
        ) : (
          <>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Hi, {me.user.name}</h1>
              <p className="text-sm text-fg-muted">{me.user.email} · member since {formatDate(me.user.created_at)}</p>
            </div>
            {error && <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
            <PlanPanel me={me} />
            <HistoryPanel items={history} paid={Boolean(me.plan)} onDelete={remove} />
            <PurchasesPanel items={purchases} />
          </>
        )}
      </main>
    </>
  );
}
