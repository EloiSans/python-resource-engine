# Documentation site

`resource-engine docs site` turns the functional documentation into a static
website for people who don't read the repository: product owners, testers,
support, the teams that consume the API or its events.

```bash
uv run resource-engine docs site --out site --title "Facturació"
python -m http.server --directory site     # or open site/index.html
```

It's built from the same model as `docs generate`, which is the code itself:
resource and state-machine docstrings, schemas, permissions and events.

| Page | Contents |
|---|---|
| `index.html` | Catalogue of resources with their description and operations. |
| `resources/<name>.html` | Description, business area and actors; operations and endpoints; filters, tenancy, versions, restricted fields; references and relations; state machines with their diagram, states, transitions and timers; documents; the emails it sends; what happens in each operation; schemas. |
| `emails.html` | Every email template set, with a preview per locale (with `[tool.resource-engine] templates`). |
| `permissions.html` | The system roles and the matrix of actions each one grants (with an authorization config). |
| `events.html` | The published events per channel, with their data; for the technical audience also each message's JSON schema and `asyncapi.json`. |
| `api.html`, `graphql.html` | Technical audience: the HTTP API from the app's OpenAPI (`openapi.json`) and the GraphQL schema (`schema.graphql`). |

Every page has the menu and a search box (press `/`) over the text of every
page. It works offline. Diagrams are drawn in the browser by Mermaid, loaded
from a CDN by default. `--mermaid-url` points at a self-hosted copy (an
intranet without internet access), and `--mermaid-url ""` leaves the
diagrams as text. Pages follow the light or dark mode of the system, adapt to
phones and print without the menu.

## What happens in each operation

Each resource page explains, per write operation, the steps that run and in
which order: input validation, required documents, the hooks before and after
the change, the commit, and the emails sent after it. Each step uses the
hook's docstring summary, and a small diagram shows the same sequence. Steps
before the commit are inside the transaction (a failure refuses the change);
steps after it run with the change saved. `after_commit` hooks run after
every change of the resource, so they are listed once; say in their
docstring which operations they act on. The technical table (hook point,
function, external effect) follows.

## Emails

With the renderer configured (`templates = "pkg.module:templates"`), the
site gets an emails page. Each template set shows which resource requests it
and when (read from the `EmailRequested` calls in hooks and actions), and a
preview per locale: the subject, the text body and the HTML body in a
sandboxed frame. Variables appear as highlighted placeholders
(`{{ customer }}`), so no data is invented. The unlocalised templates show
as the fallback for other languages. Each resource page lists its emails,
the trigger and the recipients as written in the code.

Documentation text is Markdown. Raw HTML in a docstring is shown as text,
never run.

## Audience

The same code gives two sites, chosen with `--audience` (or
`[tool.resource-engine] docs_audience`):

| | `technical` (default) | `functional` |
|---|---|---|
| For | Developers, integrators, the teams consuming the API or the events | Product owners, testers, support, business reviewers |
| Resources | Class paths, endpoints, input and output schemas, filters, concurrency, `expand`, routes, hook functions | What each resource is, its operations, states, rules, documents, emails and what happens in each operation, in plain words |
| API pages | **HTTP API** (from the app's OpenAPI: endpoints per tag, bodies, responses, schemas), the events' **payloads**, the **GraphQL schema** | — |
| Documents | `openapi.json`, `asyncapi.json`, `schema.graphql`, next to the pages | none |

The HTTP API page needs the application: `[tool.resource-engine] app =
"invoices.app:create_app"` (a FastAPI app, or a function returning one).
Building the app runs nothing: no lifespan, no database. The GraphQL page
needs `graphql = "pkg.module:schema"` (a `GraphQLSchema`, or a function
returning one). Without them, those pages are left out.

`docs generate` takes the same option for the Markdown pages.

In CI, the example's site workflow builds the audience from the manual
run's choice. On a push it uses the repository variable `DOCS_AUDIENCE`,
else `technical`:

```yaml
- name: Build the site
  env:
    AUDIENCE: ${{ inputs.audience || vars.DOCS_AUDIENCE || 'technical' }}
  run: uv run resource-engine docs site --out site --audience "$AUDIENCE"
```

To publish both, build each into its own folder (`site/` and
`site/functional/`) and deploy the parent.

## Explorer: served by the app, with a try-it console

```bash
uv run resource-engine docs site --out site --console     # at build time
```

```python
from resource_engine_fastapi import mount_docs

mount_docs(app, "site")  # http://127.0.0.1:8000/explorer/
```

With `--console`, the site gets two panels:

- **Each resource page:** a **Try it** panel. Pick an operation; its form
  has the path, query and header parameters (`If-Match`,
  `Idempotency-Key`) and a JSON body that starts from the required fields.
  The answer shows its status, timing, `Content-Language`, `ETag` and
  request id, and the body. Reading a record lists the transitions allowed
  now as buttons that open them with the id filled in. A 403 offers **Why
  was it refused?**: `/authz/explain` for policy editors, or otherwise the
  caller's roles and that action's status from `/authz/me`.
- **The home page:** a **What can I do?** panel with the caller's roles and
  every action they may perform.

**Safety:**

- **Your own token:** the console calls the API with the visitor's token,
  so it sees what that user may see and do. Permissions, tenancy and
  rate limits apply as for any client.
- **Reads only by default:** writes (create, update, delete, transitions,
  actions) need **Allow changes**.
- **Stored per tab:** the token and the switch stay in this browser tab
  only (session storage).
- **Shown as text:** answers are never rendered as HTML.
- **Headers:** `mount_docs` sends a Content-Security-Policy that lets the
  pages call only their own origin, plus `nosniff` and `no-referrer`.

**Where the site lives:**

- **Built, then served:** the site is plain files built by the CLI. The app
  only serves them, so it needs no devtools at runtime.
- **One build per audience:** a functional site with the console suits
  business reviewers; a technical one adds the API pages.
- **Hosted elsewhere:** `--api-url https://api.example.com` points the
  console at the API, which must then allow the site's origin (CORS).
- **Files the console can't send:** document uploads (multipart). Use
  another client for those.

The example mounts it when `DOCS_SITE` names the built folder:

```bash
uv run resource-engine docs site --out site --console
DOCS_SITE=site uv run uvicorn invoices.app:create_app --factory --app-dir examples
```

## Languages

With `[tool.resource-engine.i18n]` configured, the site is built once per
locale (`ca/`, `en/`, `es/`), with a language switcher on every page and a
root page that opens the visitor's language. Docstrings and descriptions
come from the application's catalogues. Headings and tables are the
generator's own texts, shipped in English, Catalan and Spanish. See
[translations](i18n.md#documentation-in-several-languages).

## Publishing

The example application's site is published at
<https://eloisans.github.io/python-resource-engine/> by
`.github/workflows/docs-site.yml` on every push to `main`. That workflow is a
template for your own application: it validates the documentation, builds the
site and deploys it to GitHub Pages (Settings → Pages → Source: GitHub
Actions).

The site is plain files: serve it from GitHub Pages, an S3 bucket, an nginx
on the intranet, or attach it to a release. In CI, build it after the
documentation checks so it always matches the code:

```yaml
- run: uv run resource-engine docs validate --strict
- run: uv run resource-engine docs site --out site
- uses: actions/upload-pages-artifact@v3
  with: {path: site}
```

The pages are generated: change the docstrings, schemas or permissions, not
the HTML.
