import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as auth from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";

const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));

vi.mock("@/lib/auth", () => ({
  CALLBACK_PATH: "/auth/callback",
  isAuthEnabled: vi.fn(),
  getCurrentUser: vi.fn(),
  signIn: vi.fn(),
  signOut: vi.fn(),
  getAccessToken: vi.fn(),
}));

const m = vi.mocked(auth);

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = "/";
});

describe("AuthGate", () => {
  it("renders the app directly when sign-in is not configured", async () => {
    m.isAuthEnabled.mockReturnValue(false);
    render(<AuthGate><p>app content</p></AuthGate>);
    expect(await screen.findByText("app content")).toBeInTheDocument();
    expect(m.getCurrentUser).not.toHaveBeenCalled();
  });

  it("asks an anonymous visitor to sign in and never shows the app", async () => {
    m.isAuthEnabled.mockReturnValue(true);
    m.getCurrentUser.mockResolvedValue(null);
    render(<AuthGate><p>app content</p></AuthGate>);
    const button = await screen.findByRole("button", { name: /sign in/i });
    expect(screen.queryByText("app content")).toBeNull();
    fireEvent.click(button);
    expect(m.signIn).toHaveBeenCalledTimes(1);
  });

  it("lets the sign-in callback page through before anyone is signed in", async () => {
    m.isAuthEnabled.mockReturnValue(true);
    m.getCurrentUser.mockResolvedValue(null);
    nav.pathname = "/auth/callback";
    render(<AuthGate><p>finishing sign-in</p></AuthGate>);
    expect(await screen.findByText("finishing sign-in")).toBeInTheDocument();
  });

  it("shows the app and who is signed in, with sign out", async () => {
    m.isAuthEnabled.mockReturnValue(true);
    m.getCurrentUser.mockResolvedValue({ name: "alice@example.com" });
    render(<AuthGate><p>app content</p></AuthGate>);
    expect(await screen.findByText("app content")).toBeInTheDocument();
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(m.signOut).toHaveBeenCalledTimes(1));
  });
});
