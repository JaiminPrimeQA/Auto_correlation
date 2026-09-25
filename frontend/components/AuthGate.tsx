"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { setTokenProvider } from "@/lib/api";
import { CALLBACK_PATH, getAccessToken, getCurrentUser, isAuthEnabled, signIn, signOut } from "@/lib/auth";
import { AppHeader } from "@/components/AppHeader";

type Status = { kind: "loading" } | { kind: "anonymous" } | { kind: "signed-in"; name: string | null };

/** Shows the app only to signed-in users when OIDC is configured, and wires
 * the access token into every API call. Without OIDC it renders the app as is. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  // Whether sign-in is configured is fixed at build time (NEXT_PUBLIC_*), so
  // without it the app renders immediately - no loading flash.
  const [status, setStatus] = useState<Status>(() =>
    isAuthEnabled() ? { kind: "loading" } : { kind: "signed-in", name: null },
  );
  const pathname = usePathname();

  // Re-checked on every route change: the sign-in callback stores the user
  // and then navigates client-side, so a check made once on first mount (on
  // the callback page, before the code exchange) would stay "anonymous".
  useEffect(() => {
    if (!isAuthEnabled()) return;
    setTokenProvider(getAccessToken);
    if (pathname === CALLBACK_PATH) return;
    let current = true;
    getCurrentUser()
      .then((user) => current && setStatus(user ? { kind: "signed-in", name: user.name } : { kind: "anonymous" }))
      .catch(() => current && setStatus({ kind: "anonymous" }));
    return () => {
      current = false;
    };
  }, [pathname]);

  // The sign-in callback must run before anyone is signed in.
  if (pathname === CALLBACK_PATH) return <>{children}</>;

  if (status.kind === "loading") {
    return (
      <>
        <AppHeader />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <p className="text-sm text-fg-muted">Loading...</p>
        </main>
      </>
    );
  }

  if (status.kind === "anonymous") {
    return (
      <>
        <AppHeader />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <div className="card mx-auto max-w-md space-y-3 p-6 text-center">
            <h1 className="text-lg font-semibold">Sign in to continue</h1>
            <p className="text-sm text-fg-muted">
              Your collections, runs and analyses are private to your account.
            </p>
            <button className="btn w-full justify-center" onClick={() => signIn()}>
              Sign in
            </button>
          </div>
        </main>
      </>
    );
  }

  return (
    <>
      {status.name && (
        <div className="mb-3 flex items-center justify-end gap-3 text-xs text-fg-muted">
          <span>{status.name}</span>
          <button className="btn-ghost text-xs" onClick={() => signOut()}>
            Sign out
          </button>
        </div>
      )}
      {children}
    </>
  );
}
