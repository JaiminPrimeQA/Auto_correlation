// Accounts, plans, checkout and saved history. The session is an httpOnly
// cookie set by the backend, so these calls carry no token themselves.
// Values mirror the backend DTOs in app/services/billing.py.

import { request, send } from "@/lib/api";

export type PlanId = "monthly" | "semiannual" | "yearly";

export interface Plan {
  id: PlanId;
  name: string;
  months: number;
  price_cents: number;
  currency: string;
  max_file_mb: number;
  retention_days: number;
}

export interface PlanCatalog {
  currency: string;
  billing_provider: "demo" | "disabled";
  free: { uses: number; max_file_mb: number };
  plans: Plan[];
}

export interface AccountUser {
  id: string;
  email: string;
  name: string;
  created_at: number;
}

export interface Subscription {
  id: string;
  plan: PlanId;
  plan_name: string;
  status: "active" | "scheduled" | "expired";
  price_cents: number;
  currency: string;
  payment_provider: string;
  payment_ref: string;
  purchased_at: number;
  starts_at: number;
  expires_at: number;
  days_left: number | null;
}

export interface Me {
  user: AccountUser | null;
  /** True when the visitor must create an account or sign in before an analysis. */
  sign_in_required: boolean;
  plan: Subscription | null;
  upcoming: Subscription[];
  limits: {
    max_file_bytes: number;
    max_file_mb: number;
    uses_limit: number | null;
    uses_used: number | null;
    uses_remaining: number | null;
    history_retention_days: number | null;
  };
}

export interface HistoryItem {
  id: string;
  collection_name: string;
  collection_filename: string | null;
  collection_bytes: number;
  request_count: number | null;
  correlation_count: number | null;
  jmx_status: string | null;
  plan: PlanId;
  created_at: number;
  expires_at: number;
  days_left: number;
  downloads: Partial<Record<"collection" | "jmx" | "manifest", string>>;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const account = {
  me: () => request<Me>("/account/me"),
  register: (name: string, email: string, password: string) =>
    request<{ user: AccountUser }>("/auth/register", json({ name, email, password })),
  login: (email: string, password: string) => request<{ user: AccountUser }>("/auth/login", json({ email, password })),
  logout: () => send<void>("/auth/logout", { method: "POST" }),
  forgotPassword: (email: string) => request<{ detail: string }>("/auth/forgot-password", json({ email })),
  resetPassword: (token: string, password: string) =>
    request<{ user: AccountUser }>("/auth/reset-password", json({ token, password })),
  plans: () => request<PlanCatalog>("/billing/plans"),
  checkout: (plan: PlanId) => request<{ subscription: Subscription }>("/billing/checkout", json({ plan })),
  subscriptions: () => request<{ items: Subscription[] }>("/billing/subscriptions"),
  history: () => request<{ items: HistoryItem[] }>("/history"),
  deleteHistory: (id: string) => send<void>(`/history/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

export function formatPrice(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
}

export function formatDate(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "per month", "per 6 months", "per year". */
export function periodLabel(months: number): string {
  if (months === 1) return "per month";
  if (months === 12) return "per year";
  return `per ${months} months`;
}

/** The monthly cost of a plan, for comparing plans. */
export function perMonth(plan: Plan): string {
  return formatPrice(Math.round(plan.price_cents / plan.months), plan.currency);
}

/** Only same-app paths: never an absolute URL from a query string. */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}
