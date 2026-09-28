"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { account, safeNext } from "@/lib/account";
import { useAccount } from "@/components/account/AccountProvider";
import { AuthCard, Field, FormError } from "@/components/account/AuthCard";

function RegisterForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"), "/pricing");
  const { refresh } = useAccount();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < 8) {
      setError("Use at least 8 characters for your password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await account.register(name, email, password);
      await refresh();
      router.push(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const query = next !== "/pricing" ? `?next=${encodeURIComponent(next)}` : "";
  return (
    <AuthCard
      title="Create your account"
      subtitle="Free to create: it includes 3 analyses with files up to 2 MB. Your plan, receipts and saved analyses live here too."
      footer={<>Already registered? <Link href={`/login${query}`} className="font-medium text-accent-soft-ink underline">Sign in</Link></>}
    >
      <form className="space-y-4" onSubmit={submit}>
        <Field id="name" label="Full name" autoComplete="name" required maxLength={80} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Field id="email" label="Email" type="email" autoComplete="email" required value={email}
          hint="Receipts and plan reminders are sent here." onChange={(e) => setEmail(e.target.value)} />
        <Field id="password" label="Password" type="password" autoComplete="new-password" required minLength={8}
          maxLength={128} value={password} hint="At least 8 characters." onChange={(e) => setPassword(e.target.value)} />
        <FormError message={error} />
        <button className="btn w-full" disabled={busy}>{busy ? "Creating account..." : "Create account"}</button>
      </form>
    </AuthCard>
  );
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterForm />
    </Suspense>
  );
}
