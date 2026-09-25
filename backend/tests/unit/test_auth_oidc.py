import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.auth import AuthError, OidcVerifier, Principal

ISSUER = "https://idp.example.com/"
AUDIENCE = "baseline11-api"


class _StaticJwks:
    """Stands in for jwt.PyJWKClient: returns the signing key for any token."""

    def __init__(self, public_key):
        self._key = public_key
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return type("SigningKey", (), {"key": self._key})()


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, other


def _token(private_key, **claims):
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123", "iat": now, "exp": now + 300}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "k1"})


def _verifier(public_key) -> OidcVerifier:
    return OidcVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_client=_StaticJwks(public_key))


def test_valid_token_yields_the_principal(keys):
    private, _ = keys
    principal = _verifier(private.public_key()).verify(_token(private, email="a@example.com"))
    assert principal == Principal(subject="user-123", email="a@example.com")


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 3600},
    {"aud": "someone-else"},
    {"iss": "https://evil.example.com/"},
    {"sub": None},
    {"nbf": int(time.time()) + 3600},
])
def test_invalid_claims_are_rejected(keys, claims):
    private, _ = keys
    with pytest.raises(AuthError):
        _verifier(private.public_key()).verify(_token(private, **claims))


def test_signature_from_another_key_is_rejected(keys):
    private, other = keys
    with pytest.raises(AuthError):
        _verifier(private.public_key()).verify(_token(other))


def test_unsigned_token_is_rejected(keys):
    private, _ = keys
    unsigned = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 60},
                          key=None, algorithm="none")
    with pytest.raises(AuthError):
        _verifier(private.public_key()).verify(unsigned)


def test_garbage_is_rejected(keys):
    private, _ = keys
    with pytest.raises(AuthError):
        _verifier(private.public_key()).verify("not-a-jwt")


def test_jwks_lookup_failure_is_an_auth_error(keys):
    class _Broken:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("unreachable")

    private, _ = keys
    verifier = OidcVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_client=_Broken())
    with pytest.raises(AuthError):
        verifier.verify(_token(private))


def test_error_messages_do_not_echo_the_token(keys):
    private, other = keys
    token = _token(other)
    with pytest.raises(AuthError) as exc:
        _verifier(private.public_key()).verify(token)
    assert token not in str(exc.value)


def test_jwks_url_defaults_to_the_discovery_document():
    from app.core.auth import discovery_url

    assert discovery_url("https://idp.example.com/") == "https://idp.example.com/.well-known/openid-configuration"
    assert discovery_url("https://idp.example.com") == "https://idp.example.com/.well-known/openid-configuration"
