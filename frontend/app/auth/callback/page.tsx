"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { completeSignIn } from "@/lib/auth";

// The identity provider redirects here with ?code=...&state=...; exchange it
// (PKCE) for tokens, then return to the page that started sign-in.
export default function AuthCallback() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return; // StrictMode runs effects twice; a code is single-use
    done.current = true;
    completeSignIn()
      .then((returnTo) => router.replace(returnTo))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [router]);

  return error ? (
    <div className="card mx-auto max-w-md space-y-3 p-6">
      <p role="alert" className="text-sm text-danger">Sign-in failed: {error}</p>
      <a className="btn-ghost" href="/">Back to the app</a>
    </div>
  ) : (
    <p className="text-sm text-fg-muted">Signing you in...</p>
  );
}
