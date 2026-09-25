"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { setTokenProvider } from "@/lib/api";
import { CALLBACK_PATH, getAccessToken, getCurrentUser, isAuthEnabled, signIn, signOut } from "@/lib/auth";

type Status = { kind: "loading" } | { kind: "anonymous" } | { kind: "signed-in"; name: string | null };

/** Shows the app only to signed-in users when OIDC is configured, and wires
 * the access token into every API call. Without OIDC it renders the app as is. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const pathname = usePathname();

  useEffect(() => {
    if (!isAuthEnabled()) {
      setStatus({ kind: "signed-in", name: null });
      return;
    }
    setTokenProvider(getAccessToken);
    getCurrentUser()
      .then((user) => setStatus(user ? { kind: "signed-in", name: user.name } : { kind: "anonymous" }))
      .catch(() => setStatus({ kind: "anonymous" }));
  }, []);

  // The sign-in callback must run before anyone is signed in.
  if (pathname === CALLBACK_PATH) return <>{children}</>;

  if (status.kind === "loading") {
    return <p className="text-sm text-slate-400">Loading…</p>;
  }

  if (status.kind === "anonymous") {
    return (
      <div className="card mx-auto max-w-md space-y-3 p-6 text-center">
        <h1 className="text-lg font-semibold">Sign in to continue</h1>
        <p className="text-sm text-slate-400">
          Your collections, runs and analyses are private to your account.
        </p>
        <button className="btn w-full justify-center" onClick={() => signIn()}>
          Sign in
        </button>
      </div>
    );
  }

  return (
    <>
      {status.name && (
        <div className="mb-3 flex items-center justify-end gap-3 text-xs text-slate-400">
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
