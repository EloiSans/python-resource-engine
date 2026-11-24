# Releasing

Every package is versioned and published on its own, under the Apache-2.0
licence. Publishing uses PyPI trusted publishing: GitHub proves the workflow's
identity to PyPI with OIDC, so no API token is stored anywhere.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests.

| Job | Runs |
|---|---|
| `test (3.12, 3.13, 3.14)` | ruff, `resource-engine check`, `docs validate --strict`, the example docs regenerated with no diff, `pytest -m "not emulator"` |
| `build` | Every wheel and sdist, then `twine check --strict` (licence, README, URLs) |
| `emulators` | The compose stack (AWS Emulator, Pub/Sub, Garage, RabbitMQ, Keycloak, OPA, OpenFGA), then `pytest -m emulator` |

**Emulator failures:** the emulator job sets `RESOURCE_ENGINE_REQUIRE_EMULATORS=1`.
A service that didn't start makes the run fail, instead of its tests being
skipped. Locally, emulator tests still skip when a service is down.

## Publishing a package

1. **Bump the version** in the package's `pyproject.toml`. Versions follow
   [PEP 440](https://peps.python.org/pep-0440/): `0.2.0`, `0.2.0rc1`,
   `0.2.0.dev1`.
2. **Commit and push** to `main`.
3. **Tag and push the tag:**

   ```bash
   git tag resource-engine-opa/v0.2.0
   git push origin resource-engine-opa/v0.2.0
   ```

`.github/workflows/release.yml` then:

1. runs the whole CI;
2. resolves the tag with `scripts/release_target.py`, which fails if the tag's
   version differs from `pyproject.toml`;
3. builds only that package and checks it;
4. publishes it from the `pypi` environment.

Tags are `<distribution>/v<version>`, one per package and version.

## One-time setup

**On PyPI**, for each package, add a *pending publisher* (Account → Publishing)
before its first release:

| Field | Value |
|---|---|
| PyPI project name | the distribution, e.g. `resource-engine-opa` |
| Owner / repository | `EloiSans` / `python-resource-engine` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

**On GitHub**, create the environment `pypi` (Settings → Environments). Add
yourself as a required reviewer if every release should wait for an approval.

Until a package has a publisher, its release fails at the last step: nothing
is uploaded, and the built files stay attached to the run.

## Versions between packages

Packages depend on each other with ranges, such as
`resource-engine>=0.1.0.dev0,<0.2`. While the core is 0.x, a minor version can
break adapters. So when the core moves to 0.2, bump the ranges of every
package that works with it in the same change, and release them after the
core. `scripts/test_packages.py` checks that every dependency between our
packages has a range, and that every package carries the licence and its
metadata.

Inside the workspace, `[tool.uv.sources]` resolves the packages to their local
folders, whatever the ranges say.
