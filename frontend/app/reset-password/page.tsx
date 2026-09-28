"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { account } from "@/lib/account";
import { useAccount } from "@/components/account/AccountProvider";
import { AuthCard, Field, FormError } from "@/components/account/AuthCard";

function ResetForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const { refresh } = useAccount();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < 8) return setError("Use at least 8 characters for your password.");
    if (password !== confirm) return setError("The two passwords do not match.");
    setBusy(true);
    setError(null);
    try {
      await account.resetPassword(token, password);
      await refresh();
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="Reset link missing" subtitle="Open the link from your email again, or request a new one.">
        <Link href="/forgot-password" className="btn w-full">Request a new link</Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password" subtitle="You will be signed in, and signed out everywhere else.">
      <form className="space-y-4" onSubmit={submit}>
        <Field id="password" label="New password" type="password" autoComplete="new-password" required minLength={8}
          maxLength={128} value={password} hint="At least 8 characters." onChange={(e) => setPassword(e.target.value)} />
        <Field id="confirm" label="Confirm new password" type="password" autoComplete="new-password" required
          value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <FormError message={error} />
        <button className="btn w-full" disabled={busy}>{busy ? "Saving..." : "Save password"}</button>
      </form>
    </AuthCard>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}
