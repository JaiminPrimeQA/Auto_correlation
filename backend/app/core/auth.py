"""OIDC bearer-token verification (spec §8: authenticate users and enforce
job ownership before production deployment).

Tokens are verified locally against the provider's JWKS: signature, issuer,
audience, expiry/not-before, and a required `sub`. The subject becomes the
owner of every job and analysis the caller creates. Error messages are fixed
strings - a token is never echoed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt


class AuthError(Exception):
    """The request's bearer token is missing or not acceptable."""


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str | None = None


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def discovery_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


class OidcVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_client: JwksClient,
        algorithms: tuple[str, ...] = ("RS256",),
        leeway_seconds: int = 30,
    ) -> None:
        if "none" in (a.lower() for a in algorithms):
            raise ValueError("The 'none' algorithm is never accepted.")
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks_client
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWKClientError as exc:
            raise AuthError("The identity provider's signing keys could not be loaded.") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("The access token is invalid or expired.") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthError("The access token has no subject.")
        email = claims.get("email")
        return Principal(subject=subject, email=email if isinstance(email, str) else None)


class DiscoveringJwksClient:
    """A PyJWKClient whose JWKS URL is read, on first use, from the issuer's
    OIDC discovery document (unless configured explicitly). The discovery
    document must name the same issuer."""

    def __init__(self, issuer: str, *, jwks_url: str | None = None, timeout_seconds: float = 5.0) -> None:
        self._issuer = issuer
        self._jwks_url = jwks_url
        self._timeout = timeout_seconds
        self._client: jwt.PyJWKClient | None = None

    def _discover(self) -> str:
        import json
        import urllib.request

        try:
            with urllib.request.urlopen(discovery_url(self._issuer), timeout=self._timeout) as resp:  # noqa: S310
                document = json.loads(resp.read())
        except (OSError, ValueError) as exc:
            raise jwt.PyJWKClientError("OIDC discovery failed.") from exc
        if str(document.get("issuer", "")).rstrip("/") != self._issuer.rstrip("/"):
            raise jwt.PyJWKClientError("OIDC discovery issuer mismatch.")
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
            raise jwt.PyJWKClientError("OIDC discovery has no HTTPS jwks_uri.")
        return jwks_uri

    def get_signing_key_from_jwt(self, token: str) -> Any:
        if self._client is None:
            url = self._jwks_url or self._discover()
            self._client = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=int(self._timeout))
        return self._client.get_signing_key_from_jwt(token)
