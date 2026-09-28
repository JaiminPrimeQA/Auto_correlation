import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { announcePlanLimit, api } from "@/lib/api";
import { account, formatPrice, perMonth, safeNext, type Me, type PlanCatalog } from "@/lib/account";
import { AccountProvider } from "@/components/account/AccountProvider";
import { AccountMenu } from "@/components/account/AccountMenu";
import { PlanLimitDialog } from "@/components/account/PlanLimitDialog";
import { PlanCards } from "@/components/account/PlanCards";
import { FilesStep } from "@/components/collection/FilesStep";
import { jsonFile } from "./fixtures";

const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => nav.pathname,
}));

const MB = 1024 * 1024;

const CATALOG: PlanCatalog = {
  currency: "USD",
  billing_provider: "demo",
  free: { uses: 3, max_file_mb: 2 },
  plans: [
    { id: "monthly", name: "Monthly", months: 1, price_cents: 900, currency: "USD", max_file_mb: 50, retention_days: 7 },
    { id: "semiannual", name: "6 months", months: 6, price_cents: 4500, currency: "USD", max_file_mb: 100, retention_days: 30 },
    { id: "yearly", name: "Yearly", months: 12, price_cents: 7900, currency: "USD", max_file_mb: 200, retention_days: 90 },
  ],
};

function freeMe(remaining = 2): Me {
  return {
    user: null,
    plan: null,
    upcoming: [],
    limits: { max_file_bytes: 2 * MB, max_file_mb: 2, uses_limit: 3, uses_used: 3 - remaining,
      uses_remaining: remaining, history_retention_days: null },
  };
}

function paidMe(): Me {
  const now = Date.now() / 1000;
  return {
    user: { id: "u1", email: "ada@example.com", name: "Ada", created_at: now },
    plan: { id: "s1", plan: "monthly", plan_name: "Monthly", status: "active", price_cents: 900, currency: "USD",
      payment_provider: "demo", payment_ref: "demo_1", purchased_at: now, starts_at: now, expires_at: now + 30 * 86400,
      days_left: 30 },
    upcoming: [],
    limits: { max_file_bytes: 50 * MB, max_file_mb: 50, uses_limit: null, uses_used: null, uses_remaining: null,
      history_retention_days: 7 },
  };
}

beforeEach(() => {
  vi.spyOn(account, "plans").mockResolvedValue(CATALOG);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("account helpers", () => {
  it("formats prices and the monthly equivalent", () => {
    expect(formatPrice(4500, "USD")).toBe("$45.00");
    expect(perMonth(CATALOG.plans[2])).toBe("$6.58");
  });

  it("only follows same-app paths after sign-in", () => {
    expect(safeNext("/checkout?plan=monthly")).toBe("/checkout?plan=monthly");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext(null, "/dashboard")).toBe("/dashboard");
  });
});

describe("AccountMenu", () => {
  it("shows the free allowance and sign-in for visitors", async () => {
    vi.spyOn(account, "me").mockResolvedValue(freeMe(2));
    render(<AccountProvider><AccountMenu /></AccountProvider>);
    expect(await screen.findByText("Free: 2 of 3 left")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "Pricing" })).toHaveAttribute("href", "/pricing");
  });

  it("shows the plan, dashboard and sign-out for a subscriber", async () => {
    vi.spyOn(account, "me").mockResolvedValue(paidMe());
    const logout = vi.spyOn(account, "logout").mockResolvedValue(undefined);
    render(<AccountProvider><AccountMenu /></AccountProvider>);
    expect(await screen.findByText("Monthly plan")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});

describe("PlanCards", () => {
  it("lists the three plans with their limits and savings", () => {
    render(<PlanCards catalog={CATALOG} />);
    const yearly = screen.getByRole("article", { name: "Yearly plan" });
    expect(yearly).toHaveTextContent("$79.00");
    expect(yearly).toHaveTextContent("Files up to 200 MB");
    expect(yearly).toHaveTextContent("History and downloads kept 90 days");
    expect(yearly).toHaveTextContent("Save 27%");
    expect(screen.getByRole("link", { name: "Choose the Monthly plan" })).toHaveAttribute("href", "/checkout?plan=monthly");
  });
});

describe("PlanLimitDialog", () => {
  it("opens with the plans when the API reports a plan limit, and closes on Escape", async () => {
    vi.spyOn(account, "me").mockResolvedValue(freeMe(0));
    render(<AccountProvider><PlanLimitDialog /></AccountProvider>);
    expect(screen.queryByRole("dialog")).toBeNull();

    act(() => announcePlanLimit({ code: "usage_limit_reached", message: "You have used all 3 free analyses." }));
    const dialog = await screen.findByRole("dialog", { name: "You have used your free analyses" });
    expect(dialog).toHaveTextContent("You have used all 3 free analyses.");
    expect(await screen.findByRole("article", { name: "6 months plan" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes when the visitor navigates, e.g. to checkout", async () => {
    vi.spyOn(account, "me").mockResolvedValue(freeMe(0));
    nav.pathname = "/";
    const { rerender } = render(<AccountProvider><PlanLimitDialog /></AccountProvider>);
    act(() => announcePlanLimit({ code: "usage_limit_reached", message: "No free analyses left." }));
    await screen.findByRole("dialog");
    nav.pathname = "/checkout";
    rerender(<AccountProvider><PlanLimitDialog /></AccountProvider>);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    nav.pathname = "/";
  });

  it("opens when an API call is refused for the plan", async () => {
    vi.spyOn(account, "me").mockResolvedValue(freeMe(0));
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).includes("/account/me")
        ? new Response(JSON.stringify(freeMe(0)), { status: 200 })
        : new Response(JSON.stringify({ code: "plan_file_limit", detail: "'big.json' is 3.0 MB." }), { status: 413 }),
    );
    render(<AccountProvider><PlanLimitDialog /></AccountProvider>);
    await expect(api.inspectCollection(jsonFile("big.json"))).rejects.toThrow("'big.json' is 3.0 MB.");
    expect(await screen.findByRole("dialog", { name: "This file is too large for your plan" })).toBeInTheDocument();
  });
});

describe("FilesStep plan limits", () => {
  it("states the free allowance and refuses an oversize file before uploading it", async () => {
    vi.spyOn(account, "me").mockResolvedValue(freeMe(2));
    const inspect = vi.spyOn(api, "inspectCollection");
    const onLimit = vi.fn();
    window.addEventListener("b11:plan-limit", onLimit);
    render(<AccountProvider><FilesStep onInspected={() => {}} /></AccountProvider>);
    expect(await screen.findByText(/2 of 3 analyses left, files up/)).toBeInTheDocument();
    expect(screen.getByText("Postman v2.0 or v2.1 JSON, up to 2 MB")).toBeInTheDocument();

    const big = new File([new Uint8Array(3 * MB)], "big.postman_collection.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText(/collection file/i), { target: { files: [big] } });
    fireEvent.click(screen.getByRole("button", { name: /inspect collection/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The free plan accepts files up to 2 MB");
    expect(inspect).not.toHaveBeenCalled();
    expect(onLimit).toHaveBeenCalledTimes(1);
    window.removeEventListener("b11:plan-limit", onLimit);
  });

  it("shows a subscriber's plan limits", async () => {
    vi.spyOn(account, "me").mockResolvedValue(paidMe());
    render(<AccountProvider><FilesStep onInspected={() => {}} /></AccountProvider>);
    expect(await screen.findByText(/unlimited analyses, files up to/)).toHaveTextContent("50 MB");
    expect(screen.getByText("Postman v2.0 or v2.1 JSON, up to 50 MB")).toBeInTheDocument();
  });
});
