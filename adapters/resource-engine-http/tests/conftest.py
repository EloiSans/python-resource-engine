"""Keycloak fixtures (see compose.emulators.yaml, realm in docker/keycloak/)."""

from __future__ import annotations

import os
import urllib.request

import pytest

KEYCLOAK = os.getenv("KEYCLOAK_URL", "http://127.0.0.1:8180")
REALM = f"{KEYCLOAK}/realms/resource-engine"
TOKEN_URL = f"{REALM}/protocol/openid-connect/token"
CLIENT_ID = "resource-engine-service"
CLIENT_SECRET = "resource-engine-dev-secret"


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"{REALM}/.well-known/openid-configuration", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("keycloak",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason=f"Keycloak not reachable at {KEYCLOAK}")
    for item in selected:
        item.add_marker(skip)
