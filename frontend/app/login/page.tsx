"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { account, safeNext } from "@/lib/account";
import { useAccount } from "@/components/account/AccountProvider";
import { AuthCard, Field, FormError } from "@/components/account/AuthCard";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"), "/dashboard");
  const { refresh } = useAccount();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await account.login(email, password);
      await refresh();
      router.push(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const query = next !== "/dashboard" ? `?next=${encodeURIComponent(next)}` : "";
  return (
    <AuthCard
      title="Sign in"
      subtitle="Welcome back. Sign in to see your plan and saved analyses."
      footer={<>New here? <Link href={`/register${query}`} className="font-medium text-accent-soft-ink underline">Create an account</Link></>}
    >
      <form className="space-y-4" onSubmit={submit}>
        <Field id="email" label="Email" type="email" autoComplete="email" required value={email}
          onChange={(e) => setEmail(e.target.value)} />
        <Field id="password" label="Password" type="password" autoComplete="current-password" required value={password}
          onChange={(e) => setPassword(e.target.value)} />
        <div className="flex justify-end">
          <Link href="/forgot-password" className="text-sm text-accent-soft-ink underline">Forgot password?</Link>
        </div>
        <FormError message={error} />
        <button className="btn w-full" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
