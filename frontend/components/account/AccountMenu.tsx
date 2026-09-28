"use client";

import Link from "next/link";
import { useAccount } from "./AccountProvider";

/** Header links: the visitor's plan at a glance, then Pricing / Sign in, or
 * Dashboard / Sign out when signed in. */
export function AccountMenu() {
  const { me, loading, signOut } = useAccount();
  if (loading || !me) return null;

  // Signed out with sign-in required: nothing is counted yet, so no chip.
  const chip = me.sign_in_required ? null : me.plan ? (
    <Link href="/dashboard" className="badge badge-accent whitespace-nowrap" title="Your plan">
      {me.plan.plan_name} plan
    </Link>
  ) : (
    <Link href="/pricing" className="badge badge-low whitespace-nowrap" title="Free analyses left">
      Free: {me.limits.uses_remaining} of {me.limits.uses_limit} left
    </Link>
  );

  return (
    <nav aria-label="Account" className="flex items-center gap-1 sm:gap-2">
      {chip}
      {me.user ? (
        <>
          <Link href="/dashboard" className="btn-ghost text-sm">Dashboard</Link>
          <button type="button" className="btn-ghost whitespace-nowrap text-sm" onClick={() => void signOut()}>
            Sign out
          </button>
        </>
      ) : (
        <>
          <Link href="/pricing" className="btn-ghost text-sm">Pricing</Link>
          <Link href="/login" className="btn-secondary whitespace-nowrap px-3 py-2 text-sm">Sign in</Link>
        </>
      )}
    </nav>
  );
}
