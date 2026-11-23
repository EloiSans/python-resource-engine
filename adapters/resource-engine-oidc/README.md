# resource-engine-oidc

Inbound authentication for Resource Engine: verifies OIDC/JWT bearer tokens and
returns a normalised `Principal`. Presets for Keycloak, Amazon Cognito, Microsoft
Entra ID, Google Identity Platform (Firebase Auth) and Google-signed ID tokens.

```python
from resource_engine_fastapi import bearer_actor
from resource_engine_oidc import keycloak

authenticator = keycloak("https://sso.example.com", "acme", audience="invoices-api")
mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))
```

This is authentication; permissions are in `resource_engine.authz` (docs/permissions.md).
Tested against Keycloak (see `compose.emulators.yaml`); the cloud presets are tested
with locally signed tokens. See [docs/identity.md](../../docs/identity.md).
Part of [Resource Engine](../../README.md).
