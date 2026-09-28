"use client";

import { useState } from "react";
import Link from "next/link";
import { account } from "@/lib/account";
import { AuthCard, Field, FormError } from "@/components/account/AuthCard";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await account.forgotPassword(email);
      setSent(r.detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      subtitle="Enter your account email and we will send you a link to choose a new password."
      footer={<Link href="/login" className="font-medium text-accent-soft-ink underline">Back to sign in</Link>}
    >
      {sent ? (
        <p role="status" className="rounded-[10px] bg-ok-soft px-3 py-2 text-sm text-ok">{sent}</p>
      ) : (
        <form className="space-y-4" onSubmit={submit}>
          <Field id="email" label="Email" type="email" autoComplete="email" required value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <FormError message={error} />
          <button className="btn w-full" disabled={busy}>{busy ? "Sending..." : "Send reset link"}</button>
        </form>
      )}
    </AuthCard>
  );
}
