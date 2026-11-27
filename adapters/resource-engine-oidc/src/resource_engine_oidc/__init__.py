"""OIDC/JWT authentication for Resource Engine (authentication only), opaque
tokens through OAuth token introspection (RFC 7662), and sender-constrained
tokens (DPoP, RFC 9449; mTLS, RFC 8705)."""

from resource_engine_oidc.introspection import (
    IntrospectionAuthenticator,
    TokenRouter,
    keycloak_introspection,
    okta_introspection,
)
from resource_engine_oidc.providers import (
    cognito,
    entra_id,
    google_id_token,
    google_identity_platform,
    keycloak,
    keycloak_principal,
)
from resource_engine_oidc.sender import (
    SenderConstrained,
    access_token_hash,
    certificate_thumbprint,
    jwk_thumbprint,
)
from resource_engine_oidc.verifier import JwksCache, OidcAuthenticator, claim_path, split

__all__ = [
    "IntrospectionAuthenticator",
    "TokenRouter",
    "keycloak_introspection",
    "keycloak_principal",
    "okta_introspection",
    "JwksCache",
    "SenderConstrained",
    "access_token_hash",
    "certificate_thumbprint",
    "jwk_thumbprint",
    "OidcAuthenticator",
    "claim_path",
    "cognito",
    "entra_id",
    "google_id_token",
    "google_identity_platform",
    "keycloak",
    "split",
]
