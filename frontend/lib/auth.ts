// OIDC sign-in (Authorization Code + PKCE) via oidc-client-ts. Enabled only
// when NEXT_PUBLIC_OIDC_AUTHORITY and NEXT_PUBLIC_OIDC_CLIENT_ID are set;
// otherwise the app runs without sign-in (local development, auth_mode=disabled).

import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

const AUTHORITY = process.env.NEXT_PUBLIC_OIDC_AUTHORITY ?? "";
const CLIENT_ID = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "";
const SCOPE = process.env.NEXT_PUBLIC_OIDC_SCOPE || "openid profile email";
// Some providers (e.g. Auth0) only issue an access token for the API when an
// audience/resource is requested.
const AUDIENCE = process.env.NEXT_PUBLIC_OIDC_AUDIENCE ?? "";

export const CALLBACK_PATH = "/auth/callback";

let manager: UserManager | null = null;

export function isAuthEnabled(): boolean {
  return Boolean(AUTHORITY && CLIENT_ID);
}

function userManager(): UserManager {
  if (!manager) {
    const origin = window.location.origin;
    manager = new UserManager({
      authority: AUTHORITY,
      client_id: CLIENT_ID,
      redirect_uri: `${origin}${CALLBACK_PATH}`,
      post_logout_redirect_uri: origin,
      response_type: "code",
      scope: SCOPE,
      // Tokens live only for this browser tab's session.
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: true,
      extraQueryParams: AUDIENCE ? { audience: AUDIENCE } : undefined,
    });
  }
  return manager;
}

export async function getCurrentUser(): Promise<{ name: string } | null> {
  if (!isAuthEnabled()) return null;
  const user: User | null = await userManager().getUser();
  if (!user || user.expired) return null;
  const profile = user.profile;
  return { name: String(profile.email ?? profile.preferred_username ?? profile.name ?? profile.sub) };
}

export async function getAccessToken(): Promise<string | null> {
  if (!isAuthEnabled()) return null;
  const user = await userManager().getUser();
  return user && !user.expired ? user.access_token : null;
}

export function signIn(): Promise<void> {
  return userManager().signinRedirect({ state: { returnTo: window.location.pathname } });
}

export async function completeSignIn(): Promise<string> {
  const user = await userManager().signinRedirectCallback();
  const state = user.state as { returnTo?: string } | undefined;
  // Only same-app paths: never an absolute URL from the state round-trip.
  return state?.returnTo?.startsWith("/") && !state.returnTo.startsWith("//") ? state.returnTo : "/";
}

export function signOut(): Promise<void> {
  return userManager().signoutRedirect();
}
