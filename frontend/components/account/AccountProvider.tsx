"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { account, type Me } from "@/lib/account";
import { isAuthEnabled, signOut as oidcSignOut } from "@/lib/auth";

interface AccountState {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<Me | null>;
  signOut: () => Promise<void>;
}

const AccountContext = createContext<AccountState>({
  me: null,
  loading: false,
  refresh: async () => null,
  signOut: async () => {},
});

/** Who the visitor is and what their plan allows, shared by the header,
 * the upload step and the account pages. Refreshed after sign-in, purchases
 * and each started analysis. */
export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await account.me();
      setMe(next);
      return next;
    } catch {
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    // With an identity provider, signing out ends that session (and leaves
    // the app); the account itself is linked to the provider's identity.
    if (isAuthEnabled()) {
      await oidcSignOut();
      return;
    }
    try {
      await account.logout();
    } finally {
      await refresh();
    }
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return <AccountContext.Provider value={{ me, loading, refresh, signOut }}>{children}</AccountContext.Provider>;
}

export function useAccount(): AccountState {
  return useContext(AccountContext);
}
